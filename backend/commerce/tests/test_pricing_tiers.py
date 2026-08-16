"""Volume breaks and the suggested retail price.

The boundary is the contract: a tier at "24 or more" must engage at 24
and not at 23, and it must engage for a buyer who ordered two cartons
rather than twenty-four packs.
"""

from datetime import date, timedelta

import pytest

from commerce import checks, services
from commerce.models import PriceTier, TradingRelationship
from core.exceptions import DomainError
from core.models import Branch, LicenceKind, LicenceStatus, PremisesLicence, User
from core.quantity import Quantity
from inventory import services as inventory
from inventory.models import MovementKind
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db


def licence(org, kind):
    branch, _ = Branch.objects.get_or_create(
        organization=org, code="MAIN", defaults={"name": "Main"}
    )
    return PremisesLicence.objects.create(
        organization=org,
        branch=branch,
        kind=kind,
        number=f"RFDA-{kind}-{org.name[:4]}",
        issued_on=date.today() - timedelta(days=400),
        expiry=date.today() + timedelta(days=365),
        status=LicenceStatus.ACTIVE,
    )


@pytest.fixture
def market():
    wholesale = make_org("ABC Wholesale", kind=LicenceKind.WHOLESALE_PHARMACY)
    retail = make_org("Kigali Care", kind=LicenceKind.RETAIL_PHARMACY)
    licence(wholesale, LicenceKind.WHOLESALE_PHARMACY)
    licence(retail, LicenceKind.RETAIL_PHARMACY)

    seller = User.objects.create_user(username="jean", password="x", organization=wholesale)
    buyer = User.objects.create_user(username="marie", password="x", organization=retail)

    product = make_product(wholesale, "Amoxicillin 500mg")
    depot = make_location(wholesale, "Depot", "DEP")
    store = make_location(retail, "Store", "MAIN")
    batch = make_batch(wholesale, product, number="A1")
    inventory.post_movement(
        organization=wholesale,
        location=depot,
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        quantity=Quantity(200, uom(product, "PACK")),
    )
    TradingRelationship.objects.create(
        organization=wholesale, customer=retail, is_verified=True, credit_limit=100_000_000
    )
    listing = services.publish_listing(
        organization=wholesale,
        product=product,
        price=10_000,
        price_uom=uom(product, "PACK"),
        offered_base=Quantity(200, uom(product, "PACK")).base_value,
        srp=14_000,
        performed_by=seller,
    )
    services.set_price_tiers(
        listing=listing,
        tiers=[(24, 9_000), (100, 8_000)],
        performed_by=seller,
    )
    listing.refresh_from_db()
    return {
        "wholesale": wholesale, "retail": retail, "seller": seller, "buyer": buyer,
        "product": product, "depot": depot, "store": store, "listing": listing,
    }


def draft(market):
    return services.start_order(
        organization=market["retail"],
        supplier=market["wholesale"],
        deliver_to=market["store"],
        performed_by=market["buyer"],
    )


class TestTierSelection:
    def test_below_the_first_break_pays_list(self, market):
        pack = uom(market["product"], "PACK")
        assert services.tier_price(market["listing"], Quantity(23, pack).base_value) == 10_000

    def test_exactly_at_the_break_gets_the_tier(self, market):
        pack = uom(market["product"], "PACK")
        assert services.tier_price(market["listing"], Quantity(24, pack).base_value) == 9_000

    def test_the_higher_break_wins_when_reached(self, market):
        pack = uom(market["product"], "PACK")
        assert services.tier_price(market["listing"], Quantity(100, pack).base_value) == 8_000

    def test_a_carton_order_qualifies_on_pack_thresholds(self, market):
        """A carton is 12 packs, so two cartons is 24 — the tier engages.

        Comparing the carton count against a pack threshold would deny it,
        which is the failure this conversion exists to prevent.
        """
        carton = uom(market["product"], "CARTON")
        assert services.tier_price(market["listing"], Quantity(2, carton).base_value) == 9_000


class TestOrderingWithTiers:
    def test_a_qualifying_order_is_priced_at_the_tier(self, market):
        order = draft(market)
        services.add_order_line(order=order, listing=market["listing"], quantity=30)
        line = order.lines.get()
        assert line.unit_price == 9_000
        assert line.line_total == 30 * 9_000

    def test_adding_across_two_clicks_still_earns_the_tier(self, market):
        """A tier is earned by what is ordered, not by how it was entered."""
        order = draft(market)
        services.add_order_line(order=order, listing=market["listing"], quantity=20)
        services.add_order_line(order=order, listing=market["listing"], quantity=10)

        assert order.lines.count() == 1
        line = order.lines.get()
        assert line.quantity == 30
        assert line.unit_price == 9_000

    def test_the_order_total_follows_the_reprice(self, market):
        order = draft(market)
        services.add_order_line(order=order, listing=market["listing"], quantity=20)
        services.add_order_line(order=order, listing=market["listing"], quantity=10)
        order.refresh_from_db()
        assert order.subtotal == 30 * 9_000

    def test_a_derived_unit_inherits_the_tier(self, market):
        """Ordering by carton at a tier price still derives from the tier."""
        order = draft(market)
        carton = uom(market["product"], "CARTON")
        services.add_order_line(
            order=order, listing=market["listing"], quantity=3, uom=carton
        )
        line = order.lines.get()
        # 3 cartons = 36 packs, so the 24+ tier applies: 9,000 × 12.
        assert line.unit_price == 9_000 * 12


class TestTierValidation:
    def test_a_tier_dearer_than_list_is_refused(self, market):
        with pytest.raises(DomainError):
            services.set_price_tiers(
                listing=market["listing"], tiers=[(24, 11_000)]
            )

    def test_a_higher_threshold_priced_above_a_lower_one_is_refused(self, market):
        """Ordering more must never cost more per unit."""
        with pytest.raises(DomainError):
            services.set_price_tiers(
                listing=market["listing"], tiers=[(24, 9_000), (100, 9_500)]
            )

    def test_a_break_at_one_is_refused(self, market):
        with pytest.raises(DomainError):
            services.set_price_tiers(listing=market["listing"], tiers=[(1, 9_000)])

    def test_setting_tiers_replaces_rather_than_appends(self, market):
        services.set_price_tiers(listing=market["listing"], tiers=[(50, 9_500)])
        assert [t.min_quantity for t in PriceTier.objects.filter(listing=market["listing"])] == [50]

    def test_tiers_out_of_order_are_accepted_and_sorted(self, market):
        services.set_price_tiers(
            listing=market["listing"], tiers=[(100, 8_000), (24, 9_000)]
        )
        thresholds = [t.min_quantity for t in market["listing"].tiers.all()]
        assert thresholds == [24, 100]


class TestBulkDiscountAlert:
    def test_one_tier_short_is_flagged_as_info(self, market):
        pack = uom(market["product"], "PACK")
        found = checks.bulk_discount_available(
            listing=market["listing"], quantity_base=Quantity(20, pack).base_value
        )
        assert [alert.code for alert in found] == ["BULK_DISCOUNT_AVAILABLE"]
        assert found[0].severity == "INFO"
        assert found[0].meta["short_by"] == 4

    def test_only_the_next_tier_is_mentioned(self, market):
        """Listing all of them turns a nudge into a price list."""
        pack = uom(market["product"], "PACK")
        found = checks.bulk_discount_available(
            listing=market["listing"], quantity_base=Quantity(20, pack).base_value
        )
        assert len(found) == 1
        assert found[0].meta["min_quantity"] == 24

    def test_at_the_top_tier_nothing_is_suggested(self, market):
        pack = uom(market["product"], "PACK")
        assert (
            checks.bulk_discount_available(
                listing=market["listing"], quantity_base=Quantity(100, pack).base_value
            )
            == []
        )


class TestSuggestedRetailPrice:
    def test_it_is_published_on_the_listing(self, market):
        assert market["listing"].srp == 14_000

    def test_repricing_without_it_does_not_withdraw_it(self, market):
        services.publish_listing(
            organization=market["wholesale"],
            product=market["product"],
            price=11_000,
            price_uom=uom(market["product"], "PACK"),
            performed_by=market["seller"],
        )
        market["listing"].refresh_from_db()
        assert market["listing"].price == 11_000
        assert market["listing"].srp == 14_000

    def test_it_travels_in_the_transfer_payload(self, market):
        from commerce import payloads

        order = draft(market)
        services.add_order_line(order=order, listing=market["listing"], quantity=5)
        services.request_approval(order=order, performed_by=market["buyer"])
        owner = User.objects.create_user(
            username="claudine", password="x", organization=market["retail"]
        )
        services.submit_order(order=order, performed_by=owner)
        services.confirm_order(order=order, performed_by=market["seller"])
        shipment = services.dispatch_order(
            order=order, from_location=market["depot"], performed_by=market["seller"]
        )

        payload = payloads.build_transfer_payload(shipment=shipment)
        assert payload["lines"][0]["srp"] == {"amount": 14_000, "uom_code": "PACK"}
