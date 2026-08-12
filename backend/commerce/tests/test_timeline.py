"""The audit spine and the order timeline.

`test_every_transition_records` is the important one. It enumerates the
transition services and asserts each leaves both records, so a
transition added later without wiring fails here rather than in an audit.
"""

from datetime import date, timedelta

import pytest

from catalog.models import LegalStatus
from commerce import services
from commerce.models import (
    ControlledTransfer,
    OrderEvent,
    PurchaseOrderStatus,
    TradingRelationship,
)
from core import audit
from core.models import (
    AuditEvent,
    Branch,
    LicenceKind,
    LicenceStatus,
    PharmacistRegistration,
    PremisesLicence,
    User,
)
from core.quantity import Quantity
from inventory import services as inventory
from inventory.models import MovementKind
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db


def licence(org, kind, *, days=365, number=None):
    branch, _ = Branch.objects.get_or_create(
        organization=org, code="MAIN", defaults={"name": "Main"}
    )
    return PremisesLicence.objects.create(
        organization=org,
        branch=branch,
        kind=kind,
        number=number or f"RFDA-{kind}-{org.name[:4]}",
        issued_on=date.today() - timedelta(days=400),
        expiry=date.today() + timedelta(days=days),
        status=LicenceStatus.ACTIVE,
    )


@pytest.fixture
def trade():
    wholesale = make_org("ABC Wholesale", kind=LicenceKind.WHOLESALE_PHARMACY)
    retail = make_org("Kigali Care", kind=LicenceKind.RETAIL_PHARMACY)
    licence(wholesale, LicenceKind.WHOLESALE_PHARMACY)
    licence(retail, LicenceKind.RETAIL_PHARMACY)

    seller = User.objects.create_user(username="jean", password="x", organization=wholesale)
    buyer = User.objects.create_user(username="marie", password="x", organization=retail)
    owner = User.objects.create_user(username="claudine", password="x", organization=retail)

    product = make_product(wholesale, "Amoxicillin 500mg", legal_status=LegalStatus.POM)
    depot = make_location(wholesale, "ABC Depot", "DEP")
    store = make_location(retail, "Main Store", "MAIN")

    batch = make_batch(wholesale, product, number="AMX-0021")
    inventory.post_movement(
        organization=wholesale,
        location=depot,
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        quantity=Quantity(20, uom(product, "PACK")),
    )
    TradingRelationship.objects.create(
        organization=wholesale, customer=retail, is_verified=True, credit_limit=100_000_000
    )
    listing = services.publish_listing(
        organization=wholesale,
        product=product,
        price=5_000,
        price_uom=uom(product, "PACK"),
        offered_base=Quantity(20, uom(product, "PACK")).base_value,
        performed_by=seller,
    )
    return {
        "wholesale": wholesale, "retail": retail, "seller": seller, "buyer": buyer,
        "owner": owner, "product": product, "depot": depot, "store": store,
        "listing": listing,
    }


def draft(trade, *, quantity=2):
    order = services.start_order(
        organization=trade["retail"],
        supplier=trade["wholesale"],
        deliver_to=trade["store"],
        performed_by=trade["buyer"],
    )
    services.add_order_line(order=order, listing=trade["listing"], quantity=quantity)
    return order


class TestAuditSpine:
    def test_record_writes_a_row(self, trade):
        event = audit.record(
            action="test.thing.happened",
            subject=trade["listing"],
            actor=trade["seller"],
            after={"price": 5_000},
        )
        assert AuditEvent.objects.filter(id=event.id).exists()
        assert event.subject_type == "commerce.VendorListing"
        assert event.subject_id == trade["listing"].id
        assert event.organization_id == trade["wholesale"].id

    def test_organization_falls_back_to_the_actor(self, trade):
        """A subject with no organization still gets attributed."""
        event = audit.record(action="test.none", actor=trade["seller"])
        assert event.organization_id == trade["wholesale"].id

    def test_snapshot_is_json_safe(self, trade):
        """Dates and UUIDs round trip; a JSONField would reject them raw."""
        snap = audit.snapshot(trade["listing"], ["price", "product", "created_at"])
        assert snap["price"] == 5_000
        assert snap["product"] == str(trade["product"].id)
        assert isinstance(snap["created_at"], str)

    def test_publishing_a_listing_is_recorded(self, trade):
        assert AuditEvent.objects.filter(action="commerce.listing.published").exists()

    def test_history_reads_back_newest_first(self, trade):
        audit.record(action="test.one", subject=trade["listing"], actor=trade["seller"])
        audit.record(action="test.two", subject=trade["listing"], actor=trade["seller"])
        actions = [event.action for event in audit.history(trade["listing"])]
        assert actions[:2] == ["test.two", "test.one"]


class TestTimeline:
    def test_requesting_approval_writes_an_event(self, trade):
        order = draft(trade)
        services.request_approval(order=order, performed_by=trade["buyer"])

        event = order.events.latest("occurred_at")
        assert event.from_status == PurchaseOrderStatus.DRAFT
        assert event.to_status == PurchaseOrderStatus.PENDING_APPROVAL
        assert event.actor_id == trade["buyer"].id
        assert event.actor_organization_id == trade["retail"].id

    def test_submission_carries_the_order_number(self, trade):
        order = draft(trade)
        services.request_approval(order=order, performed_by=trade["buyer"])
        services.submit_order(order=order, performed_by=trade["owner"])

        event = order.events.latest("occurred_at")
        assert event.to_status == PurchaseOrderStatus.SUBMITTED
        assert event.document_number == order.number
        assert event.document_number.startswith("PO-")

    def test_rejection_carries_the_reason(self, trade):
        order = draft(trade)
        services.request_approval(order=order, performed_by=trade["buyer"])
        services.reject_order(
            order=order, performed_by=trade["owner"], reason="Wrong strength."
        )
        assert order.events.latest("occurred_at").note == "Wrong strength."

    def test_both_sides_appear_on_one_timeline(self, trade):
        """The point of a separate table from AuditEvent."""
        order = draft(trade)
        services.request_approval(order=order, performed_by=trade["buyer"])
        services.submit_order(order=order, performed_by=trade["owner"])
        services.confirm_order(order=order, performed_by=trade["seller"])

        orgs = {event.actor_organization_id for event in order.events.all()}
        assert orgs == {trade["retail"].id, trade["wholesale"].id}

    def test_dispatch_records_the_delivery_note(self, trade):
        order = draft(trade)
        services.request_approval(order=order, performed_by=trade["buyer"])
        services.submit_order(order=order, performed_by=trade["owner"])
        services.confirm_order(order=order, performed_by=trade["seller"])
        shipment = services.dispatch_order(
            order=order, from_location=trade["depot"], performed_by=trade["seller"]
        )
        event = order.events.latest("occurred_at")
        assert event.to_status == PurchaseOrderStatus.DISPATCHED
        assert event.document_number == shipment.number

    def test_events_are_ordered_oldest_first(self, trade):
        order = draft(trade)
        services.request_approval(order=order, performed_by=trade["buyer"])
        services.submit_order(order=order, performed_by=trade["owner"])
        statuses = [event.to_status for event in order.events.all()]
        assert statuses == [
            PurchaseOrderStatus.PENDING_APPROVAL,
            PurchaseOrderStatus.SUBMITTED,
        ]


class TestEveryTransitionRecords:
    """Enumerated, so a new transition cannot be added unwired.

    Each entry is a transition service and the status it lands on. The
    test drives it and asserts one `OrderEvent` and one `AuditEvent`.
    """

    def test_every_transition_records(self, trade):
        order = draft(trade)
        steps = [
            (lambda: services.request_approval(order=order, performed_by=trade["buyer"]),
             PurchaseOrderStatus.PENDING_APPROVAL),
            (lambda: services.reject_order(
                order=order, performed_by=trade["owner"], reason="No."),
             PurchaseOrderStatus.REJECTED),
            (lambda: services.reopen_order(order=order, performed_by=trade["buyer"]),
             PurchaseOrderStatus.DRAFT),
            (lambda: services.request_approval(order=order, performed_by=trade["buyer"]),
             PurchaseOrderStatus.PENDING_APPROVAL),
            (lambda: services.submit_order(order=order, performed_by=trade["owner"]),
             PurchaseOrderStatus.SUBMITTED),
            (lambda: services.confirm_order(order=order, performed_by=trade["seller"]),
             PurchaseOrderStatus.CONFIRMED),
            (lambda: services.start_preparation(order=order, performed_by=trade["seller"]),
             PurchaseOrderStatus.PREPARING),
            (lambda: services.dispatch_order(
                order=order, from_location=trade["depot"], performed_by=trade["seller"]),
             PurchaseOrderStatus.DISPATCHED),
        ]

        for call, expected in steps:
            events_before = OrderEvent.objects.filter(order=order).count()
            audits_before = AuditEvent.objects.filter(
                subject_id=order.id, action__startswith="commerce.order."
            ).count()

            call()
            order.refresh_from_db()

            assert order.status == expected, f"expected {expected}, got {order.status}"
            assert OrderEvent.objects.filter(order=order).count() == events_before + 1, (
                f"{expected} left no OrderEvent — route it through _transition()"
            )
            assert (
                AuditEvent.objects.filter(
                    subject_id=order.id, action__startswith="commerce.order."
                ).count()
                == audits_before + 1
            ), f"{expected} left no AuditEvent"

    def test_no_service_assigns_status_directly(self):
        """A grep test. Cheap, and it catches the mistake at review time."""
        from pathlib import Path

        source = Path(services.__file__).read_text(encoding="utf-8")
        offenders = [
            line.strip()
            for line in source.splitlines()
            # `order.status = to_status` is the sanctioned assignment
            # inside _transition itself.
            if "order.status =" in line and "to_status" not in line
        ]
        assert offenders == [], (
            "Assign order status through _transition() so the timeline "
            f"cannot be bypassed: {offenders}"
        )


class TestControlledTransferGate:
    """A scheduled drug does not leave without two named pharmacists."""

    @pytest.fixture
    def controlled(self, trade):
        product = make_product(
            trade["wholesale"], "Morphine 10mg", legal_status=LegalStatus.CONTROLLED
        )
        batch = make_batch(trade["wholesale"], product, number="MOR-001")
        inventory.post_movement(
            organization=trade["wholesale"],
            location=trade["depot"],
            batch=batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(10, uom(product, "PACK")),
        )
        listing = services.publish_listing(
            organization=trade["wholesale"],
            product=product,
            price=20_000,
            price_uom=uom(product, "PACK"),
            offered_base=Quantity(10, uom(product, "PACK")).base_value,
            performed_by=trade["seller"],
        )
        order = services.start_order(
            organization=trade["retail"],
            supplier=trade["wholesale"],
            deliver_to=trade["store"],
            performed_by=trade["buyer"],
        )
        services.add_order_line(order=order, listing=listing, quantity=1)
        services.request_approval(order=order, performed_by=trade["buyer"])
        services.submit_order(order=order, performed_by=trade["owner"])
        services.confirm_order(order=order, performed_by=trade["seller"])
        return order

    def test_dispatch_without_a_form_is_refused(self, trade, controlled):
        with pytest.raises(services.ControlledTransferRequired):
            services.dispatch_order(
                order=controlled,
                from_location=trade["depot"],
                performed_by=trade["seller"],
            )

    def test_nothing_moved_when_it_was_refused(self, trade, controlled):
        """Checked before the ledger, not after."""
        from inventory.models import StockMovement

        before = StockMovement.objects.count()
        with pytest.raises(services.ControlledTransferRequired):
            services.dispatch_order(
                order=controlled,
                from_location=trade["depot"],
                performed_by=trade["seller"],
            )
        assert StockMovement.objects.count() == before

    def test_dispatch_with_a_released_form_succeeds(self, trade, controlled):
        registration = PharmacistRegistration.objects.create(
            organization=trade["wholesale"],
            user=trade["seller"],
            council_number="NPC-4412",
            issued_on=date.today() - timedelta(days=100),
            expiry=date.today() + timedelta(days=300),
        )
        shipment = services.dispatch_order(
            order=controlled,
            from_location=trade["depot"],
            performed_by=trade["seller"],
            controlled_transfer=registration,
        )
        transfer = ControlledTransfer.objects.get(shipment=shipment)
        assert transfer.is_released
        assert not transfer.is_complete
        assert transfer.number.startswith("CST-")

    def test_an_uncontrolled_order_needs_no_form(self, trade):
        order = draft(trade)
        services.request_approval(order=order, performed_by=trade["buyer"])
        services.submit_order(order=order, performed_by=trade["owner"])
        services.confirm_order(order=order, performed_by=trade["seller"])
        shipment = services.dispatch_order(
            order=order, from_location=trade["depot"], performed_by=trade["seller"]
        )
        assert not ControlledTransfer.objects.filter(shipment=shipment).exists()


class TestDispatchLogistics:
    def test_carrier_and_driver_are_recorded(self, trade):
        order = draft(trade)
        services.request_approval(order=order, performed_by=trade["buyer"])
        services.submit_order(order=order, performed_by=trade["owner"])
        services.confirm_order(order=order, performed_by=trade["seller"])
        shipment = services.dispatch_order(
            order=order,
            from_location=trade["depot"],
            performed_by=trade["seller"],
            carrier="Volcano Express",
            vehicle_registration="RAD 123 C",
            driver_name="Emmanuel H.",
            driver_licence="DL-88213",
        )
        assert shipment.carrier == "Volcano Express"
        assert shipment.vehicle_registration == "RAD 123 C"
        assert shipment.driver_name == "Emmanuel H."

    def test_dispatch_is_audited(self, trade):
        order = draft(trade)
        services.request_approval(order=order, performed_by=trade["buyer"])
        services.submit_order(order=order, performed_by=trade["owner"])
        services.confirm_order(order=order, performed_by=trade["seller"])
        services.dispatch_order(
            order=order, from_location=trade["depot"], performed_by=trade["seller"]
        )
        assert AuditEvent.objects.filter(action="commerce.shipment.dispatched").exists()
