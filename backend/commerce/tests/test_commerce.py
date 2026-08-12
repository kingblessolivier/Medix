"""Marketplace, orders and receiving.

The capability tests matter most: they are what makes retail and wholesale
two licensed pharmacy types sharing one core, rather than two products.
"""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from catalog.models import LegalStatus
from commerce import services
from commerce.models import (
    Availability,
    PurchaseOrder,
    GoodsReceiptStatus,
    PurchaseOrderStatus,
    ShipmentStatus,
    TradingRelationship,
    VendorListing,
)
from core.capabilities import Capability, capabilities_of, has_capability, require_capability
from core.exceptions import LicenceInvalid
from core.quantity import Quantity
from core.models import Branch, LicenceKind, LicenceStatus, Organization, PremisesLicence, User
from inventory import services as inventory
from inventory.models import MovementKind, StockStatus
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db


def licence(org, kind, *, days=365, status=LicenceStatus.ACTIVE, number=None):
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
        status=status,
    )


@pytest.fixture
def market():
    """A wholesale pharmacy and a retail pharmacy, each licensed."""
    wholesale = make_org("ABC Wholesale", kind=LicenceKind.WHOLESALE_PHARMACY)
    retail = make_org("Kigali Care Pharmacy", kind=LicenceKind.RETAIL_PHARMACY)
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

    return {
        "wholesale": wholesale,
        "retail": retail,
        "seller": seller,
        "buyer": buyer,
        "owner": owner,
        "product": product,
        "depot": depot,
        "store": store,
        "batch": batch,
    }


def release(order, market):
    """Raise for approval, then release — two people, as in the real flow."""
    services.request_approval(order=order, performed_by=market["buyer"])
    return services.submit_order(order=order, performed_by=market["owner"])


class TestCapability:
    """Held licences, never a type field. See ADR-006."""

    def test_wholesale_may_publish(self, market):
        assert has_capability(market["wholesale"], Capability.PUBLISH_LISTINGS)

    def test_retail_may_not_publish(self, market):
        assert not has_capability(market["retail"], Capability.PUBLISH_LISTINGS)

    def test_retail_may_dispense(self, market):
        assert has_capability(market["retail"], Capability.DISPENSE)

    def test_wholesale_may_not_dispense(self, market):
        assert not has_capability(market["wholesale"], Capability.DISPENSE)

    def test_one_organization_may_hold_both(self, market):
        """A wholesale pharmacy with a retail counter is common."""
        licence(market["wholesale"], LicenceKind.RETAIL_PHARMACY, number="RFDA-RET-ABC")
        granted = capabilities_of(market["wholesale"])
        assert {Capability.PUBLISH_LISTINGS, Capability.DISPENSE} <= granted

    def test_expiry_revokes_capability(self, market):
        """The behaviour a regulator expects: no one has to switch it off."""
        PremisesLicence.objects.filter(organization=market["retail"]).update(
            expiry=date.today() - timedelta(days=1)
        )
        assert not has_capability(market["retail"], Capability.DISPENSE)

    def test_suspension_revokes_capability(self, market):
        PremisesLicence.objects.filter(organization=market["retail"]).update(
            status=LicenceStatus.SUSPENDED
        )
        assert not has_capability(market["retail"], Capability.DISPENSE)

    def test_refusal_names_the_lapsed_licence(self, market):
        PremisesLicence.objects.filter(organization=market["wholesale"]).update(
            expiry=date.today() - timedelta(days=1)
        )
        with pytest.raises(LicenceInvalid) as exc:
            require_capability(market["wholesale"], Capability.PUBLISH_LISTINGS)
        assert "not valid" in str(exc.value)

    def test_refusal_when_no_licence_at_all(self, market):
        naked = Organization.objects.create(name="Unlicensed Ltd", primary_kind="RETAIL")
        with pytest.raises(LicenceInvalid):
            require_capability(naked, Capability.DISPENSE)


class TestListings:
    def test_wholesale_publishes(self, market):
        listing = services.publish_listing(
            organization=market["wholesale"],
            product=market["product"],
            price=28000,
            price_uom=uom(market["product"], "PACK"),
            moq=10,
            offered_base=2000,
        )
        assert listing.is_orderable
        assert listing.price == 28000

    def test_retail_cannot_publish(self, market):
        with pytest.raises(LicenceInvalid):
            services.publish_listing(
                organization=market["retail"],
                product=market["product"],
                price=28000,
                price_uom=uom(market["product"], "PACK"),
            )

    def test_import_on_demand_is_not_orderable(self, market):
        """A dead end becomes an import request, not an order."""
        listing = services.publish_listing(
            organization=market["wholesale"],
            product=market["product"],
            price=28000,
            price_uom=uom(market["product"], "PACK"),
            availability=Availability.IMPORT_ON_DEMAND,
        )
        assert not listing.is_orderable


class TestOrders:
    def _listing(self, market, **kwargs):
        return services.publish_listing(
            organization=market["wholesale"],
            product=market["product"],
            price=28000,
            price_uom=uom(market["product"], "PACK"),
            moq=kwargs.pop("moq", 10),
            offered_base=kwargs.pop("offered_base", 2000),
            **kwargs,
        )

    def _verified(self, market):
        return TradingRelationship.objects.create(
            organization=market["wholesale"],
            customer=market["retail"],
            is_verified=True,
            verified_at=timezone.now(),
        )

    def test_order_lifecycle(self, market):
        listing = self._listing(market)
        self._verified(market)

        order = services.start_order(
            organization=market["retail"],
            supplier=market["wholesale"],
            deliver_to=market["store"],
            performed_by=market["buyer"],
        )
        services.add_order_line(order=order, listing=listing, quantity=10)
        submitted = release(order, market)

        assert submitted.number.startswith("PO-")
        assert submitted.status == PurchaseOrderStatus.SUBMITTED
        assert submitted.subtotal == 280000

    def test_supplier_confirms(self, market):
        listing = self._listing(market)
        self._verified(market)
        order = services.start_order(
            organization=market["retail"],
            supplier=market["wholesale"],
            deliver_to=market["store"],
            performed_by=market["buyer"],
        )
        services.add_order_line(order=order, listing=listing, quantity=10)
        release(order, market)

        confirmed = services.confirm_order(order=order, performed_by=market["seller"])
        assert confirmed.status == PurchaseOrderStatus.CONFIRMED

    def test_buyer_cannot_confirm_their_own_order(self, market):
        listing = self._listing(market)
        self._verified(market)
        order = services.start_order(
            organization=market["retail"],
            supplier=market["wholesale"],
            deliver_to=market["store"],
            performed_by=market["buyer"],
        )
        services.add_order_line(order=order, listing=listing, quantity=10)
        release(order, market)

        with pytest.raises(Exception, match="supplier"):
            services.confirm_order(order=order, performed_by=market["buyer"])

    def test_unverified_customer_cannot_submit(self, market):
        """A supplier must verify a buyer's licence before supplying."""
        listing = self._listing(market)
        order = services.start_order(
            organization=market["retail"],
            supplier=market["wholesale"],
            deliver_to=market["store"],
            performed_by=market["buyer"],
        )
        services.add_order_line(order=order, listing=listing, quantity=10)
        with pytest.raises(services.CustomerNotVerified):
            release(order, market)

    def test_draft_is_reused_per_supplier(self, market):
        """Adding from the marketplace twice builds one order, not two."""
        first = services.open_draft(
            organization=market["retail"],
            supplier=market["wholesale"],
            deliver_to=market["store"],
            performed_by=market["buyer"],
        )
        second = services.open_draft(
            organization=market["retail"],
            supplier=market["wholesale"],
            deliver_to=market["store"],
            performed_by=market["buyer"],
        )
        assert first.id == second.id

    def test_submitted_order_does_not_become_the_draft(self, market):
        """A new draft opens once the last one is on its way."""
        listing = self._listing(market)
        self._verified(market)
        sent = services.open_draft(
            organization=market["retail"],
            supplier=market["wholesale"],
            deliver_to=market["store"],
            performed_by=market["buyer"],
        )
        services.add_order_line(order=sent, listing=listing, quantity=10)
        release(sent, market)

        fresh = services.open_draft(
            organization=market["retail"],
            supplier=market["wholesale"],
            deliver_to=market["store"],
            performed_by=market["buyer"],
        )
        assert fresh.id != sent.id
        assert fresh.status == PurchaseOrderStatus.DRAFT

    def test_adding_same_product_twice_merges(self, market):
        """Two adds of one product are more of it, not two lines to pick."""
        listing = self._listing(market)
        order = services.start_order(
            organization=market["retail"],
            supplier=market["wholesale"],
            deliver_to=market["store"],
            performed_by=market["buyer"],
        )
        services.add_order_line(order=order, listing=listing, quantity=10)
        services.add_order_line(order=order, listing=listing, quantity=5)

        order.refresh_from_db()
        assert order.lines.count() == 1
        line = order.lines.get()
        assert line.quantity == 15
        assert line.line_total == 15 * listing.price
        assert order.subtotal == 15 * listing.price

    def test_below_minimum_refused(self, market):
        listing = self._listing(market, moq=10)
        order = services.start_order(
            organization=market["retail"],
            supplier=market["wholesale"],
            deliver_to=market["store"],
            performed_by=market["buyer"],
        )
        with pytest.raises(services.BelowMinimum):
            services.add_order_line(order=order, listing=listing, quantity=5)

    def test_import_only_listing_refused(self, market):
        listing = self._listing(market, availability=Availability.IMPORT_ON_DEMAND)
        order = services.start_order(
            organization=market["retail"],
            supplier=market["wholesale"],
            deliver_to=market["store"],
            performed_by=market["buyer"],
        )
        with pytest.raises(services.NotOrderable):
            services.add_order_line(order=order, listing=listing, quantity=10)

    def test_empty_order_cannot_submit(self, market):
        self._verified(market)
        order = services.start_order(
            organization=market["retail"],
            supplier=market["wholesale"],
            deliver_to=market["store"],
            performed_by=market["buyer"],
        )
        with pytest.raises(Exception, match="Add a product"):
            release(order, market)


class TestReceiving:
    """Where batches enter the system."""

    def _receipt(self, market, **kwargs):
        return services.start_receipt(
            organization=market["retail"],
            location=market["store"],
            supplier=market["wholesale"],
            performed_by=market["buyer"],
            **kwargs,
        )

    def test_posting_creates_the_batch_and_moves_stock(self, market):
        receipt = self._receipt(market)
        services.add_receipt_line(
            receipt=receipt,
            product=market["product"],
            uom=uom(market["product"], "PACK"),
            received=10,
            batch_number="AMX-0099",
            expiry_date=date.today() + timedelta(days=600),
            unit_cost_base=280,
        )
        posted = services.post_receipt(receipt=receipt, performed_by=market["buyer"])

        assert posted.number.startswith("GRN-")
        assert posted.status == GoodsReceiptStatus.POSTED
        assert (
            inventory.balance_for(organization=market["retail"], product=market["product"])
            == 1000
        )

    def test_nothing_moves_until_posted(self, market):
        receipt = self._receipt(market)
        services.add_receipt_line(
            receipt=receipt,
            product=market["product"],
            uom=uom(market["product"], "PACK"),
            received=10,
            batch_number="AMX-0099",
            expiry_date=date.today() + timedelta(days=600),
        )
        assert (
            inventory.balance_for(organization=market["retail"], product=market["product"]) == 0
        )

    def test_rejected_quantity_never_enters_stock(self, market):
        """It is going back on the lorry."""
        receipt = self._receipt(market)
        services.add_receipt_line(
            receipt=receipt,
            product=market["product"],
            uom=uom(market["product"], "PACK"),
            received=10,
            rejected=2,
            rejection_reason="Damaged outer packaging",
            batch_number="AMX-0099",
            expiry_date=date.today() + timedelta(days=600),
        )
        services.post_receipt(receipt=receipt, performed_by=market["buyer"])
        assert (
            inventory.balance_for(organization=market["retail"], product=market["product"])
            == 800
        )

    def test_rejection_without_a_reason_refused(self, market):
        receipt = self._receipt(market)
        with pytest.raises(Exception, match="reason"):
            services.add_receipt_line(
                receipt=receipt,
                product=market["product"],
                uom=uom(market["product"], "PACK"),
                received=10,
                rejected=2,
                batch_number="AMX-0099",
                expiry_date=date.today() + timedelta(days=600),
            )

    def test_expired_batch_refused_at_the_door(self, market):
        receipt = self._receipt(market)
        with pytest.raises(Exception, match="expires"):
            services.add_receipt_line(
                receipt=receipt,
                product=market["product"],
                uom=uom(market["product"], "PACK"),
                received=10,
                batch_number="AMX-DEAD",
                expiry_date=date.today() - timedelta(days=1),
            )

    def test_short_delivery_produces_a_discrepancy(self, market):
        """Ordered 10, received 8. A fact, not something to correct away."""
        listing = services.publish_listing(
            organization=market["wholesale"],
            product=market["product"],
            price=28000,
            price_uom=uom(market["product"], "PACK"),
            moq=1,
            offered_base=2000,
        )
        TradingRelationship.objects.create(
            organization=market["wholesale"], customer=market["retail"], is_verified=True
        )
        order = services.start_order(
            organization=market["retail"],
            supplier=market["wholesale"],
            deliver_to=market["store"],
            performed_by=market["buyer"],
        )
        line = services.add_order_line(order=order, listing=listing, quantity=10)
        release(order, market)

        receipt = self._receipt(market, order=order)
        services.add_receipt_line(
            receipt=receipt,
            product=market["product"],
            uom=uom(market["product"], "PACK"),
            received=8,
            batch_number="AMX-0099",
            expiry_date=date.today() + timedelta(days=600),
            order_line=line,
        )
        posted = services.post_receipt(receipt=receipt, performed_by=market["buyer"])

        assert posted.has_discrepancy
        rows = services.discrepancies(posted)
        assert rows[0]["ordered"] == 10 and rows[0]["received"] == 8
        assert rows[0]["short_by"] == 2

        order.refresh_from_db()
        assert order.status == PurchaseOrderStatus.PARTIALLY_RECEIVED

    def test_full_delivery_closes_the_order(self, market):
        listing = services.publish_listing(
            organization=market["wholesale"],
            product=market["product"],
            price=28000,
            price_uom=uom(market["product"], "PACK"),
            moq=1,
            offered_base=2000,
        )
        TradingRelationship.objects.create(
            organization=market["wholesale"], customer=market["retail"], is_verified=True
        )
        order = services.start_order(
            organization=market["retail"],
            supplier=market["wholesale"],
            deliver_to=market["store"],
            performed_by=market["buyer"],
        )
        line = services.add_order_line(order=order, listing=listing, quantity=10)
        release(order, market)

        receipt = self._receipt(market, order=order)
        services.add_receipt_line(
            receipt=receipt,
            product=market["product"],
            uom=uom(market["product"], "PACK"),
            received=10,
            batch_number="AMX-0099",
            expiry_date=date.today() + timedelta(days=600),
            order_line=line,
        )
        posted = services.post_receipt(receipt=receipt, performed_by=market["buyer"])

        assert not posted.has_discrepancy
        order.refresh_from_db()
        assert order.status == PurchaseOrderStatus.RECEIVED

    def test_posting_twice_is_a_no_op(self, market):
        receipt = self._receipt(market)
        services.add_receipt_line(
            receipt=receipt,
            product=market["product"],
            uom=uom(market["product"], "PACK"),
            received=10,
            batch_number="AMX-0099",
            expiry_date=date.today() + timedelta(days=600),
        )
        first = services.post_receipt(receipt=receipt, performed_by=market["buyer"])
        second = services.post_receipt(receipt=receipt, performed_by=market["buyer"])
        assert first.number == second.number
        assert (
            inventory.balance_for(organization=market["retail"], product=market["product"])
            == 1000
        )

    def test_cold_chain_breach_quarantines_rather_than_accepts(self, market):
        """Releasing it is a separate, deliberate decision."""
        from inventory.models import StockBalance, TemperatureClass

        cold_product = make_product(market["retail"], "Insulin XYZ", cold_chain=True)
        cold_room = make_location(
            market["retail"], "Cold room", "COLD", TemperatureClass.COLD
        )
        receipt = services.start_receipt(
            organization=market["retail"],
            location=cold_room,
            supplier=market["wholesale"],
            performed_by=market["buyer"],
        )
        receipt.transport_temperature_ok = False
        receipt.save(update_fields=["transport_temperature_ok"])

        services.add_receipt_line(
            receipt=receipt,
            product=cold_product,
            uom=uom(cold_product, "PACK"),
            received=4,
            batch_number="INS-0084",
            expiry_date=date.today() + timedelta(days=300),
        )
        services.post_receipt(receipt=receipt, performed_by=market["buyer"])

        quarantined = StockBalance.objects.get(
            product=cold_product, status=StockStatus.QUARANTINED
        )
        assert quarantined.quantity_base == 400
        assert (
            inventory.balance_for(organization=market["retail"], product=cold_product) == 0
        )


class TestCatalogMirroring:
    """Receiving crosses a tenant boundary.

    A purchase order line points at the supplier's catalog row, but the
    buyer's ledger keys to the buyer's own. Getting this wrong either 404s
    on receipt or — far worse — silently stores a quantity against the
    wrong unit chain.
    """

    def test_mirror_copies_unit_chain_exactly(self, market):
        from catalog import services as catalog

        source = market["product"]
        mirrored = catalog.mirror_product(
            organization=market["retail"], source=source, performed_by=market["buyer"]
        )

        assert mirrored.organization_id == market["retail"].id
        assert mirrored.id != source.id

        # Factor for factor: a pack meaning 100 there and 12 here would
        # corrupt every received quantity rather than fail.
        assert {u.code: u.factor_to_base for u in mirrored.units.all()} == {
            u.code: u.factor_to_base for u in source.units.all()
        }
        assert mirrored.units.get(is_base=True).code == source.units.get(is_base=True).code

    def test_mirror_carries_legal_status(self, market):
        """A prescription-only medicine must not become over-the-counter."""
        from catalog import services as catalog

        mirrored = catalog.mirror_product(
            organization=market["retail"], source=market["product"], performed_by=market["buyer"]
        )
        assert mirrored.legal_status == market["product"].legal_status
        assert mirrored.requires_prescription is True

    def test_mirror_is_idempotent_on_gtin(self, market):
        """Receiving the same product twice must not fork the catalog."""
        from catalog import services as catalog

        source = market["product"]
        source.gtin = "05012345678900"
        source.save(update_fields=["gtin"])

        first = catalog.mirror_product(
            organization=market["retail"], source=source, performed_by=market["buyer"]
        )
        second = catalog.mirror_product(
            organization=market["retail"], source=source, performed_by=market["buyer"]
        )
        assert first.id == second.id

    def test_mirror_matches_on_registration_number(self, market):
        """Registration is the national identity — it wins over spelling."""
        from catalog import services as catalog
        from catalog.models import ProductRegistration

        source = market["product"]
        ProductRegistration.objects.create(
            organization=market["wholesale"],
            product=source,
            registration_number="RW-MED-4417",
        )
        # The buyer already stocks it, spelled differently.
        theirs = make_product(market["retail"], "Amoxil 500 mg caps")
        ProductRegistration.objects.create(
            organization=market["retail"],
            product=theirs,
            registration_number="RW-MED-4417",
        )

        mirrored = catalog.mirror_product(
            organization=market["retail"], source=source, performed_by=market["buyer"]
        )
        assert mirrored.id == theirs.id

    def test_own_product_is_returned_untouched(self, market):
        from catalog import services as catalog

        own = make_product(market["retail"], "Paracetamol 500mg")
        assert (
            catalog.mirror_product(
                organization=market["retail"], source=own, performed_by=market["buyer"]
            ).id
            == own.id
        )


class TestDispatch:
    """The other half of receiving.

    Receiving alone credits the buyer with stock that never left the
    seller. These tests are about the pair of ledgers agreeing.
    """

    def _confirmed_order(self, market, quantity=10):
        listing = services.publish_listing(
            organization=market["wholesale"],
            product=market["product"],
            price=28000,
            price_uom=uom(market["product"], "PACK"),
            moq=1,
            offered_base=2000,
        )
        TradingRelationship.objects.create(
            organization=market["wholesale"],
            customer=market["retail"],
            is_verified=True,
            verified_at=timezone.now(),
        )
        order = services.start_order(
            organization=market["retail"],
            supplier=market["wholesale"],
            deliver_to=market["store"],
            performed_by=market["buyer"],
        )
        services.add_order_line(order=order, listing=listing, quantity=quantity)
        release(order, market)
        services.confirm_order(order=order, performed_by=market["seller"])
        return order

    def test_dispatch_removes_stock_from_the_supplier(self, market):
        order = self._confirmed_order(market, quantity=5)
        before = inventory.balance_for(
            organization=market["wholesale"], product=market["product"]
        )

        services.dispatch_order(
            order=order, from_location=market["depot"], performed_by=market["seller"]
        )

        after = inventory.balance_for(
            organization=market["wholesale"], product=market["product"]
        )
        # 5 packs of 100 capsules.
        assert before - after == 500

    def test_dispatch_records_the_batch_it_picked(self, market):
        """The delivery note is what the receiver checks the cartons against."""
        order = self._confirmed_order(market, quantity=5)
        shipment = services.dispatch_order(
            order=order, from_location=market["depot"], performed_by=market["seller"]
        )

        assert shipment.number.startswith("DN-")
        assert shipment.status == ShipmentStatus.DISPATCHED
        line = shipment.lines.get()
        assert line.batch_number == market["batch"].batch_number
        assert line.expiry_date == market["batch"].expiry_date
        assert line.quantity_base == 500

    def test_carrier_is_recorded(self, market):
        """Who is carrying it is part of the delivery note, not decoration."""
        order = self._confirmed_order(market, quantity=5)
        shipment = services.dispatch_order(
            order=order,
            from_location=market["depot"],
            performed_by=market["seller"],
            carrier="Kigali Express",
        )
        shipment.refresh_from_db()
        assert shipment.carrier == "Kigali Express"

    def test_only_the_supplier_may_dispatch(self, market):
        order = self._confirmed_order(market)
        with pytest.raises(Exception, match="supplier"):
            services.dispatch_order(
                order=order, from_location=market["depot"], performed_by=market["buyer"]
            )

    def test_unconfirmed_order_cannot_dispatch(self, market):
        """Goods must not leave on an order the supplier never accepted."""
        listing = services.publish_listing(
            organization=market["wholesale"],
            product=market["product"],
            price=28000,
            price_uom=uom(market["product"], "PACK"),
            moq=1,
            offered_base=2000,
        )
        order = services.start_order(
            organization=market["retail"],
            supplier=market["wholesale"],
            deliver_to=market["store"],
            performed_by=market["buyer"],
        )
        services.add_order_line(order=order, listing=listing, quantity=5)
        with pytest.raises(Exception, match="confirmed"):
            services.dispatch_order(
                order=order, from_location=market["depot"], performed_by=market["seller"]
            )

    def test_short_pick_leaves_the_rest_owed(self, market):
        """A depot ships what it holds; the shortfall stays outstanding.

        Ordering more than was offered is refused outright now, so a short
        pick no longer comes from an oversized order. It comes from the
        gap between approving an order and picking it: stock written off
        for expiry in between is stock that cannot be shipped.
        """
        order = self._confirmed_order(market, quantity=20)

        # Half the shelf expires before the picker reaches it.
        inventory.post_movement(
            organization=market["wholesale"],
            location=market["depot"],
            batch=market["batch"],
            kind=MovementKind.EXPIRY_WRITE_OFF,
            quantity=Quantity(10, uom(market["product"], "PACK")),
            reason="Expired before dispatch",
        )

        shipment = services.dispatch_order(
            order=order, from_location=market["depot"], performed_by=market["seller"]
        )

        order.refresh_from_db()
        line = order.lines.get()
        assert line.dispatched_base == 1000
        assert line.undispatched_base == 1000
        assert order.status == PurchaseOrderStatus.PARTIALLY_DISPATCHED
        assert sum(l.quantity_base for l in shipment.lines.all()) == 1000

    def test_full_dispatch_closes_the_order(self, market):
        order = self._confirmed_order(market, quantity=20)
        services.dispatch_order(
            order=order, from_location=market["depot"], performed_by=market["seller"]
        )
        order.refresh_from_db()
        assert order.status == PurchaseOrderStatus.DISPATCHED
        assert order.lines.get().undispatched_base == 0

    def test_full_dispatch_closes_a_prefetched_order(self, market):
        """The viewset prefetches lines; the service must not trust that.

        Reading order.lines.all() after updating the tallies returns the
        cached pre-dispatch rows, which left a fully shipped order stuck
        on PARTIALLY_DISPATCHED. Fetched here exactly as the viewset does.
        """
        order = self._confirmed_order(market, quantity=20)
        prefetched = (
            PurchaseOrder.objects.prefetch_related("lines__product", "lines__uom")
            .get(pk=order.pk)
        )
        # Warm the prefetch cache, as serializing the order would.
        list(prefetched.lines.all())

        services.dispatch_order(
            order=prefetched, from_location=market["depot"], performed_by=market["seller"]
        )

        prefetched.refresh_from_db()
        assert prefetched.status == PurchaseOrderStatus.DISPATCHED

    def test_nothing_left_to_dispatch_is_refused(self, market):
        order = self._confirmed_order(market, quantity=20)
        services.dispatch_order(
            order=order, from_location=market["depot"], performed_by=market["seller"]
        )
        with pytest.raises(services.NothingToDispatch):
            services.dispatch_order(
                order=order, from_location=market["depot"], performed_by=market["seller"]
            )

    def test_stock_is_conserved_across_both_ledgers(self, market):
        """The point of the whole exercise.

        What leaves the supplier must equal what the buyer receives. If
        these diverge the platform is inventing or destroying medicine.
        """
        order = self._confirmed_order(market, quantity=5)
        supplier_before = inventory.balance_for(
            organization=market["wholesale"], product=market["product"]
        )
        buyer_before = inventory.balance_for(
            organization=market["retail"], product=market["product"]
        )

        shipment = services.dispatch_order(
            order=order, from_location=market["depot"], performed_by=market["seller"]
        )

        # The buyer receives exactly what the delivery note says.
        receipt = services.start_receipt(
            organization=market["retail"],
            location=market["store"],
            order=order,
            performed_by=market["buyer"],
        )
        from catalog import services as catalog

        mirrored = catalog.mirror_product(
            organization=market["retail"],
            source=market["product"],
            performed_by=market["buyer"],
        )
        for sl in shipment.lines.all():
            services.add_receipt_line(
                receipt=receipt,
                product=mirrored,
                uom=uom(mirrored, "PACK"),
                received=sl.quantity_base // 100,
                batch_number=sl.batch_number,
                expiry_date=sl.expiry_date,
                order_line=sl.order_line,
            )
        services.post_receipt(receipt=receipt, performed_by=market["buyer"])

        supplier_after = inventory.balance_for(
            organization=market["wholesale"], product=market["product"]
        )
        buyer_after = inventory.balance_for(
            organization=market["retail"], product=mirrored
        )

        left = supplier_before - supplier_after
        arrived = buyer_after - buyer_before
        assert left == 500
        assert arrived == left


class TestOffer:
    """A depot's holding and its offer are different numbers.

    Publishing the true balance would tell every customer exactly what the
    depot holds, and would let a buyer plan around stock reserved for
    somewhere else.
    """

    def _listing(self, market, offered_base=1000, **kwargs):
        return services.publish_listing(
            organization=market["wholesale"],
            product=market["product"],
            price=28000,
            price_uom=uom(market["product"], "PACK"),
            moq=1,
            offered_base=offered_base,
            **kwargs,
        )

    def test_offer_is_a_slice_of_stock_not_all_of_it(self, market):
        """Depot holds 20 packs, offers 10. The other 10 stay private."""
        listing = self._listing(market, offered_base=1000)
        on_hand = inventory.balance_for(
            organization=market["wholesale"], product=market["product"]
        )
        assert on_hand == 2000
        assert listing.offered_base == 1000
        assert listing.available_base == 1000

    def test_cannot_offer_more_than_held(self, market):
        """Publishing stock you do not have makes a buyer plan on nothing."""
        with pytest.raises(Exception, match="more than is held"):
            self._listing(market, offered_base=9999)

    def test_order_cannot_exceed_the_offer(self, market):
        listing = self._listing(market, offered_base=500)
        order = services.start_order(
            organization=market["retail"],
            supplier=market["wholesale"],
            deliver_to=market["store"],
            performed_by=market["buyer"],
        )
        with pytest.raises(services.InsufficientOffer):
            services.add_order_line(order=order, listing=listing, quantity=10)

    def test_approval_commits_the_offer(self, market):
        """Two buyers must not both be promised the last packs."""
        listing = self._listing(market, offered_base=1000)
        TradingRelationship.objects.create(
            organization=market["wholesale"], customer=market["retail"],
            is_verified=True, verified_at=timezone.now(),
        )
        order = services.start_order(
            organization=market["retail"], supplier=market["wholesale"],
            deliver_to=market["store"], performed_by=market["buyer"],
        )
        services.add_order_line(order=order, listing=listing, quantity=10)
        release(order, market)
        services.confirm_order(order=order, performed_by=market["seller"])

        listing.refresh_from_db()
        assert listing.committed_base == 1000
        assert listing.available_base == 0

    def test_dispatch_consumes_the_offer(self, market):
        """Goods that have shipped cannot be offered to anyone else."""
        listing = self._listing(market, offered_base=1000)
        TradingRelationship.objects.create(
            organization=market["wholesale"], customer=market["retail"],
            is_verified=True, verified_at=timezone.now(),
        )
        order = services.start_order(
            organization=market["retail"], supplier=market["wholesale"],
            deliver_to=market["store"], performed_by=market["buyer"],
        )
        services.add_order_line(order=order, listing=listing, quantity=10)
        release(order, market)
        services.confirm_order(order=order, performed_by=market["seller"])
        services.dispatch_order(
            order=order, from_location=market["depot"], performed_by=market["seller"]
        )

        listing.refresh_from_db()
        assert listing.offered_base == 0
        assert listing.committed_base == 0


class TestApprovalChain:
    """A pharmacist raises, someone else releases, then the depot approves."""

    def _pending(self, market):
        listing = services.publish_listing(
            organization=market["wholesale"], product=market["product"],
            price=28000, price_uom=uom(market["product"], "PACK"),
            moq=1, offered_base=2000,
        )
        TradingRelationship.objects.create(
            organization=market["wholesale"], customer=market["retail"],
            is_verified=True, verified_at=timezone.now(),
        )
        order = services.start_order(
            organization=market["retail"], supplier=market["wholesale"],
            deliver_to=market["store"], performed_by=market["buyer"],
        )
        services.add_order_line(order=order, listing=listing, quantity=5)
        services.request_approval(order=order, performed_by=market["buyer"])
        return order

    def test_raising_does_not_reach_the_depot(self, market):
        order = self._pending(market)
        assert order.status == PurchaseOrderStatus.PENDING_APPROVAL
        # Unnumbered: a purchase order number is issued on release, so a
        # draft that is never approved does not burn one.
        assert order.number == ""

    def test_raiser_cannot_approve_their_own_order(self, market):
        """Self-approval defeats the control entirely."""
        order = self._pending(market)
        with pytest.raises(services.NotApprover):
            services.submit_order(order=order, performed_by=market["buyer"])

    def test_a_colleague_releases_it(self, market):
        order = self._pending(market)
        released = services.submit_order(order=order, performed_by=market["owner"])
        assert released.status == PurchaseOrderStatus.SUBMITTED
        assert released.number.startswith("PO-")
        assert released.approved_by_id == market["owner"].id

    def test_the_depot_cannot_release_the_buyers_order(self, market):
        order = self._pending(market)
        with pytest.raises(Exception, match="buying pharmacy"):
            services.submit_order(order=order, performed_by=market["seller"])

    def test_rejection_needs_a_reason(self, market):
        order = self._pending(market)
        with pytest.raises(Exception, match="reason"):
            services.reject_order(order=order, performed_by=market["owner"], reason="  ")

    def test_rejected_order_goes_back_for_correction(self, market):
        order = self._pending(market)
        services.reject_order(
            order=order, performed_by=market["owner"], reason="Over budget this month"
        )
        assert order.status == PurchaseOrderStatus.REJECTED
        assert order.reason == "Over budget this month"
        assert order.rejected_by_id == market["owner"].id

        services.reopen_order(order=order, performed_by=market["buyer"])
        assert order.status == PurchaseOrderStatus.DRAFT

    def test_preparation_is_the_depots_step(self, market):
        order = self._pending(market)
        services.submit_order(order=order, performed_by=market["owner"])
        services.confirm_order(order=order, performed_by=market["seller"])

        with pytest.raises(Exception, match="depot"):
            services.start_preparation(order=order, performed_by=market["buyer"])

        services.start_preparation(order=order, performed_by=market["seller"])
        assert order.status == PurchaseOrderStatus.PREPARING


class TestOrderUnits:
    """A depot prices in packs; a buyer does not always want packs.

    Every rule here compares base units. Comparing a carton count against
    a pack minimum is how an order for a twelfth of the intended amount
    gets waved through.
    """

    def _listing(self, market, **kwargs):
        return services.publish_listing(
            organization=market["wholesale"],
            product=market["product"],
            price=28000,
            price_uom=uom(market["product"], "PACK"),
            moq=kwargs.pop("moq", 2),
            offered_base=kwargs.pop("offered_base", 2000),
            **kwargs,
        )

    def _order(self, market):
        return services.start_order(
            organization=market["retail"],
            supplier=market["wholesale"],
            deliver_to=market["store"],
            performed_by=market["buyer"],
        )

    def test_ordering_in_the_priced_unit_uses_the_listed_price(self, market):
        listing = self._listing(market)
        line = services.add_order_line(order=self._order(market), listing=listing, quantity=5)
        assert line.uom.code == "PACK"
        assert line.unit_price == 28000
        assert line.quantity_base == 500

    def test_ordering_by_the_carton_derives_upward(self, market):
        """A carton is 12 packs, so it costs twelve pack prices."""
        listing = self._listing(market)
        carton = uom(market["product"], "CARTON")
        line = services.add_order_line(
            order=self._order(market), listing=listing, quantity=1, uom=carton
        )
        assert line.uom.code == "CARTON"
        assert line.unit_price == 28000 * 12
        assert line.quantity_base == 1200

    def test_depot_will_not_sell_by_a_unit_it_does_not_break(self, market):
        """A wholesaler does not open a pack for one capsule.

        The wholesale seed marks loose units unsellable; the test factory
        does not, so the condition is set here explicitly.
        """
        listing = self._listing(market, moq=1)
        unit = uom(market["product"], "UNIT")
        unit.is_sellable = False
        unit.save(update_fields=["is_sellable"])

        with pytest.raises(services.NotSellable):
            services.add_order_line(
                order=self._order(market), listing=listing, quantity=50, uom=unit
            )

    def test_minimum_is_compared_in_base_units(self, market):
        """One carton clears a two-pack minimum — it is twelve of them.

        Comparing the raw numbers would see 1 < 2 and refuse an order
        six times larger than the minimum.
        """
        listing = self._listing(market, moq=2)
        carton = uom(market["product"], "CARTON")
        line = services.add_order_line(
            order=self._order(market), listing=listing, quantity=1, uom=carton
        )
        assert line.quantity_base == 1200

    def test_allocation_is_compared_in_base_units(self, market):
        """500 base offered is five packs, and not one carton."""
        listing = self._listing(market, offered_base=500, moq=1)
        carton = uom(market["product"], "CARTON")
        with pytest.raises(services.InsufficientOffer):
            services.add_order_line(
                order=self._order(market), listing=listing, quantity=1, uom=carton
            )

    def test_two_units_of_one_product_are_two_lines(self, market):
        """A carton plus a loose pack is a real order, not a merge."""
        listing = self._listing(market, moq=1)
        order = self._order(market)
        services.add_order_line(
            order=order, listing=listing, quantity=1, uom=uom(market["product"], "CARTON")
        )
        services.add_order_line(
            order=order, listing=listing, quantity=3, uom=uom(market["product"], "PACK")
        )
        order.refresh_from_db()
        assert order.lines.count() == 2
        assert {l.uom.code for l in order.lines.all()} == {"CARTON", "PACK"}
        assert order.subtotal == (28000 * 12) + (28000 * 3)

    def test_same_unit_twice_merges(self, market):
        listing = self._listing(market, moq=1)
        order = self._order(market)
        services.add_order_line(order=order, listing=listing, quantity=2)
        services.add_order_line(order=order, listing=listing, quantity=3)
        order.refresh_from_db()
        assert order.lines.count() == 1
        assert order.lines.get().quantity == 5

    def test_zero_and_negative_are_refused(self, market):
        listing = self._listing(market, moq=1)
        for bad in (0, -3):
            with pytest.raises(Exception, match="positive"):
                services.add_order_line(order=self._order(market), listing=listing, quantity=bad)

    def test_a_unit_from_another_product_is_refused(self, market):
        listing = self._listing(market, moq=1)
        other = make_product(market["wholesale"], "Paracetamol 500mg")
        with pytest.raises(Exception, match="another product"):
            services.add_order_line(
                order=self._order(market), listing=listing, quantity=1, uom=uom(other, "PACK")
            )
