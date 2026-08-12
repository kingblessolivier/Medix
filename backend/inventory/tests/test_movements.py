"""Transfer, quarantine, returns and recall.

The recall tests carry the weight. Pulling a batch off our own shelves is
the easy half; the list of who already has it is the half that matters,
and it is the reason movements record a location and sale lines record a
batch.
"""

from datetime import date, timedelta

import pytest

from catalog.models import LegalStatus
from core.exceptions import DomainError
from core.models import User
from core.quantity import Quantity
from inventory import movements, services
from inventory.models import MovementKind, StockStatus, TemperatureClass
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db


@pytest.fixture
def pharmacy():
    org = make_org("Kigali Care")
    user = User.objects.create_user(username="marie", password="x", organization=org)
    product = make_product(org, "Amoxicillin 500mg")
    front = make_location(org, "Front counter", "FRONT")
    store = make_location(org, "Back store", "STORE")
    batch = make_batch(org, product, number="AMX-1")
    services.post_movement(
        organization=org,
        location=store,
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        quantity=Quantity(10, uom(product, "PACK")),
    )
    return {
        "org": org, "user": user, "product": product,
        "front": front, "store": store, "batch": batch,
    }


def at(pharmacy, location, *, status=StockStatus.AVAILABLE):
    return services.balance_for(
        organization=pharmacy["org"],
        product=pharmacy["product"],
        location=location,
        status=status,
    )


class TestTransfer:
    def test_stock_moves_between_locations(self, pharmacy):
        movements.transfer(
            organization=pharmacy["org"],
            batch=pharmacy["batch"],
            from_location=pharmacy["store"],
            to_location=pharmacy["front"],
            quantity=Quantity(3, uom(pharmacy["product"], "PACK")),
            performed_by=pharmacy["user"],
        )
        assert at(pharmacy, pharmacy["store"]) == 700
        assert at(pharmacy, pharmacy["front"]) == 300

    def test_it_is_two_movements_sharing_a_reference(self, pharmacy):
        """One row with a location change leaves neither balance rebuildable."""
        result = movements.transfer(
            organization=pharmacy["org"],
            batch=pharmacy["batch"],
            from_location=pharmacy["store"],
            to_location=pharmacy["front"],
            quantity=Quantity(3, uom(pharmacy["product"], "PACK")),
            performed_by=pharmacy["user"],
        )
        assert result["out"].movement.kind == MovementKind.TRANSFER_OUT
        assert result["in"].movement.kind == MovementKind.TRANSFER_IN
        assert result["out"].movement.reference == result["in"].movement.reference
        assert result["reference"].startswith("TRF-")

    def test_the_total_held_is_unchanged(self, pharmacy):
        before = services.balance_for(
            organization=pharmacy["org"], product=pharmacy["product"]
        )
        movements.transfer(
            organization=pharmacy["org"],
            batch=pharmacy["batch"],
            from_location=pharmacy["store"],
            to_location=pharmacy["front"],
            quantity=Quantity(4, uom(pharmacy["product"], "PACK")),
            performed_by=pharmacy["user"],
        )
        assert services.balance_for(
            organization=pharmacy["org"], product=pharmacy["product"]
        ) == before

    def test_transferring_to_the_same_location_is_refused(self, pharmacy):
        with pytest.raises(movements.SameLocation):
            movements.transfer(
                organization=pharmacy["org"],
                batch=pharmacy["batch"],
                from_location=pharmacy["store"],
                to_location=pharmacy["store"],
                quantity=Quantity(1, uom(pharmacy["product"], "PACK")),
                performed_by=pharmacy["user"],
            )

    def test_more_than_is_held_is_refused(self, pharmacy):
        from core.exceptions import InsufficientStock

        with pytest.raises(InsufficientStock):
            movements.transfer(
                organization=pharmacy["org"],
                batch=pharmacy["batch"],
                from_location=pharmacy["store"],
                to_location=pharmacy["front"],
                quantity=Quantity(99, uom(pharmacy["product"], "PACK")),
                performed_by=pharmacy["user"],
            )

    def test_cold_chain_is_enforced_on_arrival(self, pharmacy):
        """One enforcement point, in post_movement, not two that drift."""
        from inventory.services import ColdChainViolation

        cold = make_product(pharmacy["org"], "Insulin", cold_chain=True)
        fridge = make_location(
            pharmacy["org"], "Fridge", "FRIDGE", temperature_class=TemperatureClass.COLD
        )
        batch = make_batch(pharmacy["org"], cold, number="INS-1", cold_chain=True)
        services.post_movement(
            organization=pharmacy["org"],
            location=fridge,
            batch=batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(5, uom(cold, "PACK")),
        )
        with pytest.raises(ColdChainViolation):
            movements.transfer(
                organization=pharmacy["org"],
                batch=batch,
                from_location=fridge,
                to_location=pharmacy["front"],
                quantity=Quantity(1, uom(cold, "PACK")),
                performed_by=pharmacy["user"],
            )

    def test_a_cross_organization_transfer_is_refused(self, pharmacy):
        """That is a sale, and it goes through dispatch."""
        other = make_org("Someone Else")
        elsewhere = make_location(other, "Their store", "THEIRS")
        with pytest.raises(DomainError):
            movements.transfer(
                organization=pharmacy["org"],
                batch=pharmacy["batch"],
                from_location=pharmacy["store"],
                to_location=elsewhere,
                quantity=Quantity(1, uom(pharmacy["product"], "PACK")),
                performed_by=pharmacy["user"],
            )


class TestQuarantine:
    def test_it_moves_between_statuses_not_out_of_stock(self, pharmacy):
        movements.quarantine(
            organization=pharmacy["org"],
            batch=pharmacy["batch"],
            location=pharmacy["store"],
            quantity=Quantity(4, uom(pharmacy["product"], "PACK")),
            performed_by=pharmacy["user"],
            reason="Damaged outer cartons.",
        )
        assert at(pharmacy, pharmacy["store"]) == 600
        assert at(pharmacy, pharmacy["store"], status=StockStatus.QUARANTINED) == 400

    def test_quarantined_stock_is_not_allocated(self, pharmacy):
        """The whole point: held stock must not go out of the door."""
        movements.quarantine(
            organization=pharmacy["org"],
            batch=pharmacy["batch"],
            location=pharmacy["store"],
            quantity=Quantity(10, uom(pharmacy["product"], "PACK")),
            performed_by=pharmacy["user"],
            reason="Recall pending.",
        )
        from core.exceptions import InsufficientStock

        with pytest.raises(InsufficientStock):
            services.allocate_fefo(
                organization=pharmacy["org"],
                product=pharmacy["product"],
                location=pharmacy["store"],
                quantity=Quantity(1, uom(pharmacy["product"], "PACK")),
            )

    def test_it_needs_a_reason(self, pharmacy):
        with pytest.raises(DomainError):
            movements.quarantine(
                organization=pharmacy["org"],
                batch=pharmacy["batch"],
                location=pharmacy["store"],
                quantity=Quantity(1, uom(pharmacy["product"], "PACK")),
                performed_by=pharmacy["user"],
                reason="  ",
            )


class TestSupplierReturn:
    def test_stock_leaves(self, pharmacy):
        movements.supplier_return(
            organization=pharmacy["org"],
            batch=pharmacy["batch"],
            location=pharmacy["store"],
            quantity=Quantity(2, uom(pharmacy["product"], "PACK")),
            performed_by=pharmacy["user"],
            reason="Short-dated on arrival.",
        )
        assert at(pharmacy, pharmacy["store"]) == 800

    def test_it_can_send_back_quarantined_stock_directly(self, pharmacy):
        """Forcing a release first would put unusable stock into available."""
        movements.quarantine(
            organization=pharmacy["org"],
            batch=pharmacy["batch"],
            location=pharmacy["store"],
            quantity=Quantity(5, uom(pharmacy["product"], "PACK")),
            performed_by=pharmacy["user"],
            reason="Arrived warm.",
        )
        movements.supplier_return(
            organization=pharmacy["org"],
            batch=pharmacy["batch"],
            location=pharmacy["store"],
            quantity=Quantity(5, uom(pharmacy["product"], "PACK")),
            performed_by=pharmacy["user"],
            reason="Rejected, going back.",
            status=StockStatus.QUARANTINED,
        )
        assert at(pharmacy, pharmacy["store"], status=StockStatus.QUARANTINED) == 0
        assert at(pharmacy, pharmacy["store"]) == 500


class TestRecall:
    def test_it_pulls_from_every_location_at_once(self, pharmacy):
        """A recall run store by store is a recall that misses one."""
        movements.transfer(
            organization=pharmacy["org"],
            batch=pharmacy["batch"],
            from_location=pharmacy["store"],
            to_location=pharmacy["front"],
            quantity=Quantity(4, uom(pharmacy["product"], "PACK")),
            performed_by=pharmacy["user"],
        )
        result = movements.recall(
            organization=pharmacy["org"],
            batch=pharmacy["batch"],
            performed_by=pharmacy["user"],
            reason="Manufacturer notice RW-2026-04.",
        )
        assert at(pharmacy, pharmacy["store"]) == 0
        assert at(pharmacy, pharmacy["front"]) == 0
        assert result["locations"] == 2
        assert result["quantity_base"] == 1_000

    def test_recalled_stock_sits_in_its_own_status(self, pharmacy):
        movements.recall(
            organization=pharmacy["org"],
            batch=pharmacy["batch"],
            performed_by=pharmacy["user"],
            reason="Contamination.",
        )
        assert at(pharmacy, pharmacy["store"], status=StockStatus.RECALLED) == 1_000

    def test_quarantined_stock_is_recalled_too(self, pharmacy):
        """Nothing is left behind because it happened to be on hold."""
        movements.quarantine(
            organization=pharmacy["org"],
            batch=pharmacy["batch"],
            location=pharmacy["store"],
            quantity=Quantity(3, uom(pharmacy["product"], "PACK")),
            performed_by=pharmacy["user"],
            reason="Under review.",
        )
        movements.recall(
            organization=pharmacy["org"],
            batch=pharmacy["batch"],
            performed_by=pharmacy["user"],
            reason="Contamination.",
        )
        assert at(pharmacy, pharmacy["store"], status=StockStatus.QUARANTINED) == 0
        assert at(pharmacy, pharmacy["store"], status=StockStatus.RECALLED) == 1_000

    def test_it_is_numbered(self, pharmacy):
        result = movements.recall(
            organization=pharmacy["org"],
            batch=pharmacy["batch"],
            performed_by=pharmacy["user"],
            reason="Contamination.",
        )
        assert result["reference"].startswith("REC-")

    def test_it_needs_a_reason(self, pharmacy):
        with pytest.raises(DomainError):
            movements.recall(
                organization=pharmacy["org"],
                batch=pharmacy["batch"],
                performed_by=pharmacy["user"],
                reason="",
            )

    def test_it_is_audited_with_the_trace_counts(self, pharmacy):
        from core.models import AuditEvent

        movements.recall(
            organization=pharmacy["org"],
            batch=pharmacy["batch"],
            performed_by=pharmacy["user"],
            reason="Contamination.",
        )
        event = AuditEvent.objects.get(action="inventory.batch.recalled")
        assert event.after["batch"] == "AMX-1"
        assert "patients" in event.after


class TestTrace:
    def test_it_reports_stock_still_held(self, pharmacy):
        trace = movements.trace_batch(
            organization=pharmacy["org"], batch=pharmacy["batch"]
        )
        assert trace["on_hand_base"] == 1_000
        assert trace["patients"] == []
        assert trace["customers"] == []

    def test_it_names_the_patients_it_was_dispensed_to(self, pharmacy):
        """The half of a recall that actually protects anyone."""
        from core.models import Branch
        from sales import services as sales
        from sales.models import Patient, TaxRule

        branch, _ = Branch.objects.get_or_create(
            organization=pharmacy["org"], code="MAIN", defaults={"name": "Main"}
        )
        TaxRule.objects.create(
            organization=pharmacy["org"],
            treatment="STANDARD",
            rate_basis_points=1800,
            effective_from=date.today() - timedelta(days=30),
        )
        patient = Patient.objects.create(
            organization=pharmacy["org"], full_name="Aline M.", phone="0788000000"
        )
        # Sold over the counter: the trace is what is under test, not the
        # prescription gate, and a POM would need a verified script.
        pharmacy["product"].legal_status = LegalStatus.OTC
        pharmacy["product"].save(update_fields=["legal_status"])
        sale = sales.start_sale(
            organization=pharmacy["org"],
            branch=branch,
            location=pharmacy["store"],
            cashier=pharmacy["user"],
        )
        sale.patient = patient
        sale.save(update_fields=["patient"])
        sales.add_line(
            sale=sale,
            product=pharmacy["product"],
            quantity=2,
            uom=uom(pharmacy["product"], "PACK"),
            unit_price=1_000,
        )
        sales.complete_sale(sale=sale, performed_by=pharmacy["user"])

        trace = movements.trace_batch(
            organization=pharmacy["org"], batch=pharmacy["batch"]
        )
        assert len(trace["patients"]) == 1
        assert trace["patients"][0]["patient"] == "Aline M."
        assert trace["patients"][0]["phone"] == "0788000000"
        assert trace["dispensed_base"] == 200
