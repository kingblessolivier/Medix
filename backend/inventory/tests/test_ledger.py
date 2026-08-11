"""Ledger integrity — mandatory test group 1. See docs/15-testing.md.

The replay test is the one that matters most: it catches an entire class
of bug that no unit test of a single service will.
"""

from datetime import timedelta

import pytest
from django.db.models import Sum

from core.exceptions import InsufficientStock
from core.quantity import Quantity
from inventory import services
from inventory.models import MovementKind, StockBalance, StockMovement, StockStatus
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup():
    org = make_org()
    location = make_location(org)
    product = make_product(org)
    batch = make_batch(org, product)
    return org, location, product, batch


def receive(setup, packs: int, **kwargs):
    org, location, product, batch = setup
    return services.post_movement(
        organization=org,
        location=location,
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        quantity=Quantity(packs, uom(product, "PACK")),
        **kwargs,
    )


class TestAppendOnly:
    def test_movement_cannot_be_updated(self, setup):
        result = receive(setup, 5)
        result.movement.quantity_base = 999
        with pytest.raises(RuntimeError, match="append-only"):
            result.movement.save()

    def test_movement_cannot_be_deleted(self, setup):
        result = receive(setup, 5)
        with pytest.raises(RuntimeError, match="append-only"):
            result.movement.delete()

    def test_correction_is_a_compensating_movement(self, setup):
        """The only sanctioned way to fix a mistake."""
        org, location, product, batch = setup
        receive(setup, 5)
        services.post_movement(
            organization=org,
            location=location,
            batch=batch,
            kind=MovementKind.ADJUSTMENT,
            quantity=Quantity(-200, uom(product, "UNIT")),
            reason="Miscount at receiving",
        )
        assert StockMovement.objects.count() == 2
        assert services.balance_for(organization=org, product=product) == 300


class TestBalances:
    def test_movements_sum_to_balance(self, setup):
        org, location, product, batch = setup
        receive(setup, 5)
        services.issue_fefo(
            organization=org,
            product=product,
            location=location,
            quantity=Quantity(20, uom(product, "UNIT")),
            kind=MovementKind.SALE,
        )
        ledger = StockMovement.objects.filter(batch=batch).aggregate(t=Sum("quantity_base"))["t"]
        projection = StockBalance.objects.get(
            batch=batch, location=location, status=StockStatus.AVAILABLE
        ).quantity_base
        assert ledger == projection == 480

    def test_balance_after_matches_running_sum(self, setup):
        org, location, product, batch = setup
        receive(setup, 3)
        receive(setup, 2)
        services.issue_fefo(
            organization=org,
            product=product,
            location=location,
            quantity=Quantity(50, uom(product, "UNIT")),
            kind=MovementKind.SALE,
        )
        running = 0
        for movement in StockMovement.objects.filter(batch=batch).order_by("recorded_at"):
            running += movement.quantity_base
            assert movement.balance_after_base == running

    def test_replay_from_zero_reproduces_state(self, setup):
        """Rebuild every balance from movements and assert equality."""
        org, location, product, batch = setup
        receive(setup, 8)
        services.issue_fefo(
            organization=org,
            product=product,
            location=location,
            quantity=Quantity(137, uom(product, "UNIT")),
            kind=MovementKind.SALE,
        )
        before = services.balance_for(organization=org, product=product)

        # Corrupt the projection, then rebuild from the ledger.
        StockBalance.objects.filter(batch=batch).update(quantity_base=999999)
        corrected = services.rebuild_balances(organization=org)

        assert corrected == 1
        assert services.balance_for(organization=org, product=product) == before

    def test_ledger_is_authoritative_when_they_disagree(self, setup):
        org, location, product, batch = setup
        receive(setup, 4)
        StockBalance.objects.filter(batch=batch).update(quantity_base=1)
        ledger = services.ledger_balance_for(
            organization=org, batch=batch, location=location, status=StockStatus.AVAILABLE
        )
        assert ledger == 400
        services.rebuild_balances(organization=org)
        assert services.balance_for(organization=org, product=product) == 400


class TestGuards:
    def test_cannot_go_negative(self, setup):
        org, location, product, batch = setup
        receive(setup, 1)
        with pytest.raises(InsufficientStock):
            services.post_movement(
                organization=org,
                location=location,
                batch=batch,
                kind=MovementKind.SALE,
                quantity=Quantity(101, uom(product, "UNIT")),
            )

    def test_zero_quantity_rejected(self, setup):
        org, location, product, batch = setup
        with pytest.raises(Exception):
            services.post_movement(
                organization=org,
                location=location,
                batch=batch,
                kind=MovementKind.PURCHASE_RECEIPT,
                quantity=Quantity(0, uom(product, "UNIT")),
            )

    def test_sign_is_derived_from_kind_not_caller(self, setup):
        """A caller passing a positive quantity to a SALE still reduces."""
        org, location, product, batch = setup
        receive(setup, 5)
        services.post_movement(
            organization=org,
            location=location,
            batch=batch,
            kind=MovementKind.SALE,
            quantity=Quantity(20, uom(product, "UNIT")),
        )
        assert services.balance_for(organization=org, product=product) == 480


class TestIdempotency:
    def test_same_key_applies_once(self, setup):
        """An agent may retry indefinitely after losing its connection."""
        first = receive(setup, 5, idempotency_key="agent-retry-1")
        second = receive(setup, 5, idempotency_key="agent-retry-1")

        assert second.replayed is True
        assert second.movement.id == first.movement.id
        assert StockMovement.objects.count() == 1

    def test_different_keys_both_apply(self, setup):
        org, _, product, _ = setup
        receive(setup, 5, idempotency_key="a")
        receive(setup, 5, idempotency_key="b")
        assert services.balance_for(organization=org, product=product) == 1000


class TestColdChain:
    def test_cold_batch_rejected_in_ambient_location(self):
        org = make_org()
        ambient = make_location(org, "Main Store", "MAIN")
        product = make_product(org, "Insulin XYZ", cold_chain=True)
        batch = make_batch(org, product, number="INS-0084", cold_chain=True)

        with pytest.raises(services.ColdChainViolation):
            services.post_movement(
                organization=org,
                location=ambient,
                batch=batch,
                kind=MovementKind.PURCHASE_RECEIPT,
                quantity=Quantity(10, uom(product, "UNIT")),
            )

    def test_cold_batch_accepted_in_cold_room(self):
        from inventory.models import TemperatureClass

        org = make_org()
        cold = make_location(org, "Cold room", "COLD", TemperatureClass.COLD)
        product = make_product(org, "Insulin XYZ", cold_chain=True)
        batch = make_batch(org, product, number="INS-0084", cold_chain=True)

        result = services.post_movement(
            organization=org,
            location=cold,
            batch=batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(10, uom(product, "UNIT")),
        )
        assert result.balance_after_base == 10


class TestExpiryReporting:
    def test_expiring_batches_ordered_nearest_first(self):
        org = make_org()
        location = make_location(org)
        product = make_product(org)
        for number, days in [("FAR", 900), ("NEAR", 20), ("MID", 200)]:
            batch = make_batch(org, product, number=f"AMX-{number}", expires_in_days=days)
            services.post_movement(
                organization=org,
                location=location,
                batch=batch,
                kind=MovementKind.PURCHASE_RECEIPT,
                quantity=Quantity(1, uom(product, "PACK")),
            )

        within_90 = list(services.expiring_batches(organization=org, within_days=90))
        assert [b.batch.batch_number for b in within_90] == ["AMX-NEAR"]

        within_365 = list(services.expiring_batches(organization=org, within_days=365))
        assert [b.batch.batch_number for b in within_365] == ["AMX-NEAR", "AMX-MID"]
