"""Stock take — counting the room, and the correction that follows.

Two tests carry the design. `test_the_count_does_not_move_stock` is the
control a stock take exists to provide: a clipboard must not be able to
rewrite a balance. And `test_a_sale_during_the_count_is_not_a_variance`
is the one that decides whether anybody trusts the result — a pharmacy
does not close to count, and a counter who counted correctly must not be
made to explain somebody else's sale.
"""

import pytest

from core.exceptions import DomainError
from core.models import User
from core.quantity import Quantity
from inventory import counting, services
from inventory.counting import CountStatus
from inventory.models import MovementKind, StockMovement
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db


@pytest.fixture
def room():
    org = make_org("Kigali Care")
    counter = User.objects.create_user(username="marie", password="x", organization=org)
    owner = User.objects.create_user(username="claudine", password="x", organization=org)
    store = make_location(org, "Main Store", "MAIN")

    product = make_product(org, "Amoxicillin 500mg")
    batch = make_batch(org, product, number="AMX-1")
    services.post_movement(
        organization=org,
        location=store,
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        quantity=Quantity(10, uom(product, "PACK")),
    )
    return {
        "org": org, "counter": counter, "owner": owner, "store": store,
        "product": product, "batch": batch,
    }


def open_count(room):
    return counting.open_count(
        organization=room["org"], location=room["store"], performed_by=room["counter"]
    )


def on_hand(room):
    return services.balance_for(
        organization=room["org"], product=room["product"], location=room["store"]
    )


class TestCounting:
    def test_a_count_starts_against_a_room(self, room):
        count = open_count(room)
        assert count.status == CountStatus.COUNTING
        assert count.reference.startswith("SC-")

    def test_two_counts_of_one_room_are_refused(self, room):
        """Two sets of adjustments against the same shelves would each
        correct a figure the other had already corrected."""
        open_count(room)
        with pytest.raises(DomainError) as raised:
            open_count(room)
        assert raised.value.code == "count_in_progress"

    def test_counting_records_what_was_found(self, room):
        count = open_count(room)
        line = counting.record_count(count=count, batch=room["batch"], counted_base=960)

        assert line.expected_base == 1_000
        assert line.counted_base == 960
        assert line.variance_base == -40

    def test_recounting_replaces_rather_than_adds(self, room):
        """A counter who miscounted and went back is the ordinary case."""
        count = open_count(room)
        counting.record_count(count=count, batch=room["batch"], counted_base=900)
        counting.record_count(count=count, batch=room["batch"], counted_base=1_000)

        assert count.lines.count() == 1
        assert count.lines.get().counted_base == 1_000

    def test_the_count_does_not_move_stock(self, room):
        """The control this exists to provide. A clipboard must not be
        able to rewrite a balance."""
        count = open_count(room)
        counting.record_count(
            count=count, batch=room["batch"], counted_base=500, reason="Half missing"
        )
        counting.submit_count(count=count, performed_by=room["counter"])

        assert on_hand(room) == 1_000
        assert not StockMovement.objects.filter(kind=MovementKind.ADJUSTMENT).exists()

    def test_a_negative_count_is_refused(self, room):
        count = open_count(room)
        with pytest.raises(DomainError):
            counting.record_count(count=count, batch=room["batch"], counted_base=-5)


class TestTheSaleThatHappensMidCount:
    def test_a_sale_during_the_count_is_not_a_variance(self, room):
        """A pharmacy does not close to count.

        Expected is frozen when the line is counted. Reading it at
        approval time would show a sale made while the counter was three
        aisles away as a discrepancy — and the person asked to explain it
        would be the one who counted correctly.
        """
        count = open_count(room)
        counting.record_count(count=count, batch=room["batch"], counted_base=1_000)

        # Two packs sold while the counter was elsewhere.
        services.post_movement(
            organization=room["org"],
            location=room["store"],
            batch=room["batch"],
            kind=MovementKind.SALE,
            quantity=Quantity(2, uom(room["product"], "PACK")),
        )

        line = count.lines.get()
        assert line.expected_base == 1_000
        assert line.variance_base == 0

    def test_and_approving_posts_nothing_for_it(self, room):
        count = open_count(room)
        counting.record_count(count=count, batch=room["batch"], counted_base=1_000)
        services.post_movement(
            organization=room["org"],
            location=room["store"],
            batch=room["batch"],
            kind=MovementKind.SALE,
            quantity=Quantity(2, uom(room["product"], "PACK")),
        )
        counting.submit_count(count=count, performed_by=room["counter"])
        result = counting.approve_count(count=count, performed_by=room["owner"])

        assert result["adjusted"] == 0
        assert on_hand(room) == 800


class TestVarianceNeedsExplaining:
    def test_a_large_difference_blocks_submission(self, room):
        count = open_count(room)
        counting.record_count(count=count, batch=room["batch"], counted_base=800)

        with pytest.raises(DomainError) as raised:
            counting.submit_count(count=count, performed_by=room["counter"])
        assert raised.value.code == "variance_unexplained"

    def test_a_reason_lets_it_through(self, room):
        count = open_count(room)
        counting.record_count(
            count=count, batch=room["batch"], counted_base=800, reason="Water damage"
        )
        assert (
            counting.submit_count(count=count, performed_by=room["counter"]).status
            == CountStatus.SUBMITTED
        )

    def test_a_cheap_difference_needs_none(self, room):
        """Counting error at the margins is normal and not worth a form.

        Fifteen capsules at 280 is 4,200 — under the threshold, so it
        passes without anybody typing "counting error" into a box.
        """
        count = open_count(room)
        counting.record_count(count=count, batch=room["batch"], counted_base=985)
        assert counting.submit_count(count=count, performed_by=room["counter"])

    def test_the_threshold_is_value_not_count(self, room):
        """The same shortfall on expensive stock does need explaining."""
        room["batch"].unit_cost_base = 40_000
        room["batch"].save(update_fields=["unit_cost_base"])

        count = open_count(room)
        counting.record_count(count=count, batch=room["batch"], counted_base=999)
        with pytest.raises(DomainError) as raised:
            counting.submit_count(count=count, performed_by=room["counter"])
        assert raised.value.code == "variance_unexplained"

    def test_an_empty_count_cannot_be_submitted(self, room):
        count = open_count(room)
        with pytest.raises(DomainError) as raised:
            counting.submit_count(count=count, performed_by=room["counter"])
        assert raised.value.code == "count_empty"


class TestApproval:
    def submitted(self, room, counted=960, reason="Counting error on the top shelf"):
        count = open_count(room)
        counting.record_count(
            count=count, batch=room["batch"], counted_base=counted, reason=reason
        )
        counting.submit_count(count=count, performed_by=room["counter"])
        return count

    def test_approving_reconciles_the_ledger(self, room):
        count = self.submitted(room)
        result = counting.approve_count(count=count, performed_by=room["owner"])

        assert result["adjusted"] == 1
        assert result["net_base"] == -40
        assert on_hand(room) == 960

    def test_it_moves_stock_through_the_ledger(self, room):
        """No path here writes a balance directly."""
        count = self.submitted(room)
        counting.approve_count(count=count, performed_by=room["owner"])

        movement = StockMovement.objects.get(kind=MovementKind.ADJUSTMENT)
        assert movement.quantity_base == -40
        assert movement.reference == count.reference

    def test_a_surplus_adjusts_upward_too(self, room):
        count = self.submitted(room, counted=1_040, reason="Found behind the shelf")
        counting.approve_count(count=count, performed_by=room["owner"])
        assert on_hand(room) == 1_040

    def test_the_counter_cannot_approve_their_own_sheet(self, room):
        count = self.submitted(room)
        with pytest.raises(DomainError) as raised:
            counting.approve_count(count=count, performed_by=room["counter"])
        assert raised.value.code == "not_approver"

    def test_a_sole_pharmacist_can(self, room):
        """A control nobody can satisfy produces a second shared login."""
        count = self.submitted(room)
        User.objects.filter(organization=room["org"]).exclude(
            pk=room["counter"].pk
        ).update(is_active=False)

        counting.approve_count(count=count, performed_by=room["counter"])
        count.refresh_from_db()
        assert "Self-approved" in count.note

    def test_approving_twice_is_refused(self, room):
        count = self.submitted(room)
        counting.approve_count(count=count, performed_by=room["owner"])
        with pytest.raises(DomainError) as raised:
            counting.approve_count(count=count, performed_by=room["owner"])
        assert raised.value.code == "not_submitted"

    def test_it_is_audited(self, room):
        from core.models import AuditEvent

        count = self.submitted(room)
        counting.approve_count(count=count, performed_by=room["owner"])
        assert AuditEvent.objects.filter(action="inventory.count.approved").exists()


class TestCancelling:
    def test_an_abandoned_count_moves_nothing(self, room):
        count = open_count(room)
        counting.record_count(count=count, batch=room["batch"], counted_base=1)
        counting.cancel_count(
            count=count, performed_by=room["counter"], reason="Wrong room."
        )

        assert count.status == CountStatus.CANCELLED
        assert on_hand(room) == 1_000

    def test_a_reason_is_required(self, room):
        count = open_count(room)
        with pytest.raises(DomainError):
            counting.cancel_count(count=count, performed_by=room["counter"], reason=" ")

    def test_an_approved_count_cannot_be_cancelled(self, room):
        count = open_count(room)
        counting.record_count(count=count, batch=room["batch"], counted_base=1_000)
        counting.submit_count(count=count, performed_by=room["counter"])
        counting.approve_count(count=count, performed_by=room["owner"])

        with pytest.raises(DomainError) as raised:
            counting.cancel_count(
                count=count, performed_by=room["counter"], reason="Changed my mind."
            )
        assert raised.value.code == "already_approved"

    def test_cancelling_frees_the_room(self, room):
        count = open_count(room)
        counting.cancel_count(count=count, performed_by=room["counter"], reason="Wrong room.")
        assert open_count(room)


class TestApi:
    @pytest.fixture
    def client(self, room):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=room["counter"])
        return client

    def test_the_whole_trip(self, client, room):
        from rest_framework.test import APIClient

        opened = client.post(
            "/api/v1/stock-counts/", {"location": str(room["store"].id)}, format="json"
        )
        assert opened.status_code == 201
        count_id = opened.data["id"]

        counted = client.post(
            f"/api/v1/stock-counts/{count_id}/lines/",
            {
                "batch": str(room["batch"].id),
                "counted_base": 970,
                "reason": "Counting error on the top shelf",
            },
            format="json",
        )
        assert counted.data["lines"][0]["variance_base"] == -30

        submitted = client.post(
            f"/api/v1/stock-counts/{count_id}/submit/", {}, format="json"
        )
        assert submitted.data["status"] == "SUBMITTED"

        owner = APIClient()
        owner.force_authenticate(user=room["owner"])
        approved = owner.post(
            f"/api/v1/stock-counts/{count_id}/approve/", {}, format="json"
        )
        assert approved.data["adjusted"] == 1
        assert on_hand(room) == 970

    def test_another_tenant_sees_nothing(self, client, room):
        from rest_framework.test import APIClient

        open_count(room)
        other = make_org("ABC Wholesale", kind="WHOLESALE")
        stranger = User.objects.create_user(
            username="jean", password="x", organization=other
        )
        theirs = APIClient()
        theirs.force_authenticate(user=stranger)

        assert theirs.get("/api/v1/stock-counts/").data["count"] == 0
