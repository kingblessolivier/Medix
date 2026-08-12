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
    GoodsReceiptStatus,
    PurchaseOrderStatus,
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
        "product": product,
        "depot": depot,
        "store": store,
        "batch": batch,
    }


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

    def test_comparison_shows_the_tradeoff(self, market):
        """Cheapest is frequently the wrong choice, so show the rest."""
        other = make_org("MedSupply", kind=LicenceKind.WHOLESALE_PHARMACY)
        licence(other, LicenceKind.WHOLESALE_PHARMACY, number="RFDA-WH-MED")
        # Same catalogue product, offered by a second vendor.
        services.publish_listing(
            organization=market["wholesale"],
            product=market["product"],
            price=28000,
            price_uom=uom(market["product"], "PACK"),
            moq=10,
            lead_time_days=1,
        )
        services.publish_listing(
            organization=other,
            product=market["product"],
            price=27500,
            price_uom=uom(market["product"], "PACK"),
            moq=20,
            lead_time_days=3,
        )

        rows = services.compare_vendors(product=market["product"])
        assert [r["price"] for r in rows] == [27500, 28000]
        cheapest = rows[0]
        assert cheapest["moq"] == 20 and cheapest["lead_time_days"] == 3


class TestOrders:
    def _listing(self, market, **kwargs):
        return services.publish_listing(
            organization=market["wholesale"],
            product=market["product"],
            price=28000,
            price_uom=uom(market["product"], "PACK"),
            moq=kwargs.pop("moq", 10),
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
        submitted = services.submit_order(order=order, performed_by=market["buyer"])

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
        services.submit_order(order=order, performed_by=market["buyer"])

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
        services.submit_order(order=order, performed_by=market["buyer"])

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
            services.submit_order(order=order, performed_by=market["buyer"])

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
        services.submit_order(order=sent, performed_by=market["buyer"])

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
            services.submit_order(order=order, performed_by=market["buyer"])


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
        services.submit_order(order=order, performed_by=market["buyer"])

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
        services.submit_order(order=order, performed_by=market["buyer"])

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
