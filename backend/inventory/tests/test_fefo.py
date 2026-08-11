"""FEFO — mandatory test group 2. See docs/15-testing.md.

Getting this wrong does not throw. It silently generates expiry
write-offs, discovered months later as loss.
"""

from datetime import date, timedelta

import pytest

from core.exceptions import InsufficientStock
from core.quantity import Quantity
from inventory import services
from inventory.models import MovementKind, StockBalance, StockStatus
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db


@pytest.fixture
def stocked():
    """Three batches, deliberately received in the wrong order.

    The nearest-expiry batch is created last, so anything picking by
    insertion order or primary key fails these tests.
    """
    org = make_org()
    location = make_location(org)
    product = make_product(org)

    far = make_batch(org, product, number="AMX-FAR", expires_in_days=900)
    mid = make_batch(org, product, number="AMX-MID", expires_in_days=400)
    near = make_batch(org, product, number="AMX-NEAR", expires_in_days=30)

    for batch, packs in [(far, 5), (mid, 3), (near, 2)]:
        services.post_movement(
            organization=org,
            location=location,
            batch=batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(packs, uom(product, "PACK")),
        )
    return org, location, product, {"far": far, "mid": mid, "near": near}


class TestOrdering:
    def test_picks_nearest_expiry_first(self, stocked):
        org, location, product, batches = stocked
        allocations = services.allocate_fefo(
            organization=org,
            product=product,
            location=location,
            quantity=Quantity(50, uom(product, "UNIT")),
        )
        assert len(allocations) == 1
        assert allocations[0].batch.batch_number == "AMX-NEAR"

    def test_not_insertion_order(self, stocked):
        """AMX-FAR was received first. FIFO would pick it; FEFO must not."""
        org, location, product, _ = stocked
        allocations = services.allocate_fefo(
            organization=org,
            product=product,
            location=location,
            quantity=Quantity(10, uom(product, "UNIT")),
        )
        assert allocations[0].batch.batch_number != "AMX-FAR"

    def test_spans_batches_in_expiry_order(self, stocked):
        """200 units: exhausts NEAR (200), then would spill into MID."""
        org, location, product, _ = stocked
        allocations = services.allocate_fefo(
            organization=org,
            product=product,
            location=location,
            quantity=Quantity(250, uom(product, "UNIT")),
        )
        assert [(a.batch.batch_number, a.quantity_base) for a in allocations] == [
            ("AMX-NEAR", 200),
            ("AMX-MID", 50),
        ]

    def test_allocation_sums_to_request(self, stocked):
        org, location, product, _ = stocked
        allocations = services.allocate_fefo(
            organization=org,
            product=product,
            location=location,
            quantity=Quantity(640, uom(product, "UNIT")),
        )
        assert sum(a.quantity_base for a in allocations) == 640


class TestExclusions:
    def test_skips_expired_batches(self, stocked):
        org, location, product, _ = stocked
        expired = make_batch(org, product, number="AMX-DEAD", expires_in_days=-1)
        # Receive against it while it is still in date, then let it lapse.
        StockBalance.objects.create(
            organization=org,
            location=location,
            batch=expired,
            product=product,
            status=StockStatus.AVAILABLE,
            quantity_base=500,
            expiry_date=expired.expiry_date,
        )
        allocations = services.allocate_fefo(
            organization=org,
            product=product,
            location=location,
            quantity=Quantity(10, uom(product, "UNIT")),
        )
        assert all(a.batch.batch_number != "AMX-DEAD" for a in allocations)

    def test_skips_quarantined_stock(self, stocked):
        org, location, product, batches = stocked
        StockBalance.objects.filter(
            batch=batches["near"], status=StockStatus.AVAILABLE
        ).update(status=StockStatus.QUARANTINED)

        allocations = services.allocate_fefo(
            organization=org,
            product=product,
            location=location,
            quantity=Quantity(10, uom(product, "UNIT")),
        )
        assert allocations[0].batch.batch_number == "AMX-MID"

    def test_skips_empty_batches(self, stocked):
        org, location, product, batches = stocked
        StockBalance.objects.filter(batch=batches["near"]).update(quantity_base=0)
        allocations = services.allocate_fefo(
            organization=org,
            product=product,
            location=location,
            quantity=Quantity(10, uom(product, "UNIT")),
        )
        assert allocations[0].batch.batch_number == "AMX-MID"

    def test_scoped_to_location(self, stocked):
        org, _, product, _ = stocked
        other = make_location(org, "Remera", "RMR")
        with pytest.raises(InsufficientStock):
            services.allocate_fefo(
                organization=org,
                product=product,
                location=other,
                quantity=Quantity(1, uom(product, "UNIT")),
            )


class TestFailure:
    def test_raises_rather_than_partially_allocating(self, stocked):
        """1,000 units exist. Asking for 1,001 must not return 1,000."""
        org, location, product, _ = stocked
        with pytest.raises(InsufficientStock) as exc:
            services.allocate_fefo(
                organization=org,
                product=product,
                location=location,
                quantity=Quantity(1001, uom(product, "UNIT")),
            )
        assert exc.value.meta["available_base"] == 1000
        assert exc.value.meta["requested_base"] == 1001

    def test_nothing_posted_on_failure(self, stocked):
        org, location, product, _ = stocked
        before = services.balance_for(organization=org, product=product)
        with pytest.raises(InsufficientStock):
            services.issue_fefo(
                organization=org,
                product=product,
                location=location,
                quantity=Quantity(5000, uom(product, "UNIT")),
                kind=MovementKind.SALE,
            )
        assert services.balance_for(organization=org, product=product) == before


class TestIssue:
    def test_issue_posts_one_movement_per_batch(self, stocked):
        org, location, product, _ = stocked
        results = services.issue_fefo(
            organization=org,
            product=product,
            location=location,
            quantity=Quantity(250, uom(product, "UNIT")),
            kind=MovementKind.SALE,
            reference="SAL-2026-00982",
        )
        assert len(results) == 2
        assert services.balance_for(organization=org, product=product) == 750

    def test_override_records_a_reason(self, stocked):
        """A manual batch choice is permitted, and is logged."""
        org, location, product, batches = stocked
        result = services.post_movement(
            organization=org,
            location=location,
            batch=batches["far"],
            kind=MovementKind.SALE,
            quantity=Quantity(10, uom(product, "UNIT")),
            reason="Customer requested longer-dated stock",
        )
        assert result.movement.batch_id == batches["far"].id
        assert result.movement.reason


class TestPartialPack:
    def test_six_capsules_from_a_pack_of_a_hundred(self, stocked):
        """The normal case in this market, not an edge case."""
        org, location, product, _ = stocked
        services.issue_fefo(
            organization=org,
            product=product,
            location=location,
            quantity=Quantity(6, uom(product, "UNIT")),
            kind=MovementKind.SALE,
        )
        assert services.balance_for(organization=org, product=product) == 994

    def test_packs_and_units_reconcile_against_one_batch(self, stocked):
        org, location, product, batches = stocked
        services.post_movement(
            organization=org,
            location=location,
            batch=batches["near"],
            kind=MovementKind.SALE,
            quantity=Quantity(1, uom(product, "PACK")),
        )
        services.post_movement(
            organization=org,
            location=location,
            batch=batches["near"],
            kind=MovementKind.SALE,
            quantity=Quantity(6, uom(product, "UNIT")),
        )
        remaining = StockBalance.objects.get(
            batch=batches["near"], status=StockStatus.AVAILABLE
        ).quantity_base
        assert remaining == 200 - 100 - 6
