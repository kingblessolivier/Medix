"""Controlled substance quotas and the regulator extract.

The quota counts **throughput, not holding**. What the regulator caps is
how much of a narcotic passed through the premises; a snapshot of the
shelf would let a pharmacy cycle far more than its quota while never
appearing to hold much.
"""

from datetime import date, timedelta

import pytest

from catalog.models import LegalStatus
from commerce import services
from commerce.models import TradingRelationship
from core import extracts, quotas
from core.alerts import AcknowledgementRequired, AlertBlocked, Severity
from core.models import (
    Branch,
    LicenceKind,
    LicenceStatus,
    PharmacistRegistration,
    PremisesLicence,
    User,
)
from core.quantity import Quantity
from core.quotas import ControlledQuota, QuotaPeriod
from inventory import services as inventory
from inventory.models import MovementKind
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db

TODAY = date.today()


def licence(org, kind):
    branch, _ = Branch.objects.get_or_create(
        organization=org, code="MAIN", defaults={"name": "Main"}
    )
    return PremisesLicence.objects.create(
        organization=org,
        branch=branch,
        kind=kind,
        number=f"RFDA-{kind}-{org.name[:4]}",
        issued_on=TODAY - timedelta(days=400),
        expiry=TODAY + timedelta(days=365),
        status=LicenceStatus.ACTIVE,
    )


@pytest.fixture
def depot():
    wholesale = make_org("ABC Wholesale", kind=LicenceKind.WHOLESALE_PHARMACY)
    retail = make_org("Kigali Care", kind=LicenceKind.RETAIL_PHARMACY)
    licence(wholesale, LicenceKind.WHOLESALE_PHARMACY)
    licence(retail, LicenceKind.RETAIL_PHARMACY)

    seller = User.objects.create_user(username="jean", password="x", organization=wholesale)
    buyer = User.objects.create_user(username="marie", password="x", organization=retail)
    owner = User.objects.create_user(username="claudine", password="x", organization=retail)

    product = make_product(wholesale, "Morphine 10mg", legal_status=LegalStatus.CONTROLLED)
    warehouse = make_location(wholesale, "Depot", "DEP")
    store = make_location(retail, "Store", "MAIN")

    batch = make_batch(wholesale, product, number="MOR-1")
    inventory.post_movement(
        organization=wholesale,
        location=warehouse,
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        quantity=Quantity(50, uom(product, "PACK")),  # 5,000 base
    )
    TradingRelationship.objects.create(
        organization=wholesale, customer=retail, is_verified=True, credit_limit=100_000_000
    )
    registration = PharmacistRegistration.objects.create(
        organization=wholesale,
        user=seller,
        council_number="NPC-4412",
        issued_on=TODAY - timedelta(days=100),
        expiry=TODAY + timedelta(days=300),
    )
    listing = services.publish_listing(
        organization=wholesale,
        product=product,
        price=20_000,
        price_uom=uom(product, "PACK"),
        offered_base=Quantity(50, uom(product, "PACK")).base_value,
        performed_by=seller,
    )
    return {
        "wholesale": wholesale, "retail": retail, "seller": seller, "buyer": buyer,
        "owner": owner, "product": product, "warehouse": warehouse, "store": store,
        "listing": listing, "registration": registration,
    }


def quota(depot, *, limit_base, schedule="I", period=QuotaPeriod.MONTH):
    return ControlledQuota.objects.create(
        organization=depot["wholesale"],
        schedule=schedule,
        period=period,
        limit_base=limit_base,
        effective_from=TODAY - timedelta(days=30),
    )


def ship(depot, *, packs, acknowledged=None):
    order = services.start_order(
        organization=depot["retail"],
        supplier=depot["wholesale"],
        deliver_to=depot["store"],
        performed_by=depot["buyer"],
    )
    services.add_order_line(order=order, listing=depot["listing"], quantity=packs)
    services.request_approval(order=order, performed_by=depot["buyer"])
    services.submit_order(order=order, performed_by=depot["owner"])
    services.confirm_order(order=order, performed_by=depot["seller"])
    return services.dispatch_order(
        order=order,
        from_location=depot["warehouse"],
        performed_by=depot["seller"],
        controlled_transfer=depot["registration"],
        acknowledged=acknowledged,
    )


class TestPeriodBounds:
    def test_a_month_runs_to_its_own_last_day(self):
        start, end = quotas.period_bounds(QuotaPeriod.MONTH, date(2026, 2, 14))
        assert start == date(2026, 2, 1)
        assert end == date(2026, 2, 28)

    def test_a_leap_february_is_handled(self):
        _, end = quotas.period_bounds(QuotaPeriod.MONTH, date(2028, 2, 14))
        assert end == date(2028, 2, 29)

    def test_a_quarter_spans_three_months(self):
        start, end = quotas.period_bounds(QuotaPeriod.QUARTER, date(2026, 5, 20))
        assert start == date(2026, 4, 1)
        assert end == date(2026, 6, 30)

    def test_a_year_is_the_calendar_year(self):
        start, end = quotas.period_bounds(QuotaPeriod.YEAR, date(2026, 8, 12))
        assert (start, end) == (date(2026, 1, 1), date(2026, 12, 31))


class TestQuotaCheck:
    def test_no_quota_recorded_means_the_check_does_not_apply(self, depot):
        """Not capped is not the same as barred from trading."""
        assert quotas.check(
            organization=depot["wholesale"], schedule="I", pending_base=99_999_999
        ) == []

    def test_under_the_limit_is_silent(self, depot):
        quota(depot, limit_base=10_000)
        assert quotas.check(
            organization=depot["wholesale"], schedule="I", pending_base=1_000
        ) == []

    def test_at_eighty_percent_it_warns(self, depot):
        quota(depot, limit_base=10_000)
        found = quotas.check(
            organization=depot["wholesale"], schedule="I", pending_base=8_000
        )
        assert [alert.code for alert in found] == ["CONTROLLED_QUOTA_NEAR"]
        assert found[0].severity == Severity.WARNING

    def test_over_the_limit_blocks(self, depot):
        quota(depot, limit_base=10_000)
        found = quotas.check(
            organization=depot["wholesale"], schedule="I", pending_base=10_001
        )
        assert [alert.code for alert in found] == ["CONTROLLED_QUOTA_EXCEEDED"]
        assert found[0].severity == Severity.CRITICAL

    def test_the_pending_consignment_counts(self, depot):
        """Counting only history lets a depot at its limit ship for ever."""
        quota(depot, limit_base=3_000)
        ship(depot, packs=20, acknowledged=["CONTROLLED_QUOTA_NEAR"])  # 2,000 base

        standing = quotas.position(
            organization=depot["wholesale"], schedule="I", pending_base=1_500
        )
        assert standing.used_base == 2_000
        assert standing.exposure == 3_500
        assert standing.remaining == 0

    def test_only_outward_movement_counts(self, depot):
        """A receipt is not throughput; the fixture received 5,000 base."""
        quota(depot, limit_base=4_000)
        standing = quotas.position(organization=depot["wholesale"], schedule="I")
        assert standing.used_base == 0


class TestDatedQuotas:
    def test_the_limit_in_force_on_the_day_applies(self, depot):
        ControlledQuota.objects.create(
            organization=depot["wholesale"],
            schedule="I",
            limit_base=1_000,
            effective_from=TODAY - timedelta(days=200),
            effective_to=TODAY - timedelta(days=100),
        )
        ControlledQuota.objects.create(
            organization=depot["wholesale"],
            schedule="I",
            limit_base=9_000,
            effective_from=TODAY - timedelta(days=99),
        )
        then = quotas.quota_for(
            organization=depot["wholesale"],
            schedule="I",
            as_of=TODAY - timedelta(days=150),
        )
        now = quotas.quota_for(organization=depot["wholesale"], schedule="I")
        assert then.limit_base == 1_000
        assert now.limit_base == 9_000


class TestDispatchEnforcesQuota:
    def test_a_consignment_over_quota_is_refused(self, depot):
        quota(depot, limit_base=1_000)
        with pytest.raises(AlertBlocked):
            ship(depot, packs=20)  # 2,000 base

    def test_nothing_moved_when_it_was_refused(self, depot):
        from inventory.models import StockMovement

        quota(depot, limit_base=1_000)
        before = StockMovement.objects.count()
        with pytest.raises(AlertBlocked):
            ship(depot, packs=20)
        assert StockMovement.objects.count() == before

    def test_approaching_the_quota_needs_acknowledging(self, depot):
        quota(depot, limit_base=2_000)
        with pytest.raises(AcknowledgementRequired):
            ship(depot, packs=18)  # 1,800 base — 90%

    def test_acknowledged_it_ships(self, depot):
        quota(depot, limit_base=2_000)
        shipment = ship(depot, packs=18, acknowledged=["CONTROLLED_QUOTA_NEAR"])
        assert shipment.number.startswith("DN-")

    def test_an_uncontrolled_order_is_unaffected(self, depot):
        quota(depot, limit_base=1)
        plain = make_product(depot["wholesale"], "Paracetamol 500mg")
        batch = make_batch(depot["wholesale"], plain, number="PAR-1")
        inventory.post_movement(
            organization=depot["wholesale"],
            location=depot["warehouse"],
            batch=batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(10, uom(plain, "PACK")),
        )
        listing = services.publish_listing(
            organization=depot["wholesale"],
            product=plain,
            price=2_000,
            price_uom=uom(plain, "PACK"),
            offered_base=Quantity(10, uom(plain, "PACK")).base_value,
            performed_by=depot["seller"],
        )
        order = services.start_order(
            organization=depot["retail"],
            supplier=depot["wholesale"],
            deliver_to=depot["store"],
            performed_by=depot["buyer"],
        )
        services.add_order_line(order=order, listing=listing, quantity=5)
        services.request_approval(order=order, performed_by=depot["buyer"])
        services.submit_order(order=order, performed_by=depot["owner"])
        services.confirm_order(order=order, performed_by=depot["seller"])
        shipment = services.dispatch_order(
            order=order,
            from_location=depot["warehouse"],
            performed_by=depot["seller"],
        )
        assert shipment.number


class TestRegulatorExtract:
    def test_transfers_are_listed_with_their_state(self, depot):
        ship(depot, packs=5)
        found = extracts.controlled_transfers(
            organization=depot["wholesale"],
            start=TODAY - timedelta(days=1),
            end=TODAY,
        )
        assert len(found["transfers"]) == 1
        # Released but not yet counter-signed — which is the finding.
        assert found["incomplete"] == 1
        assert found["transfers"][0]["released_registration"] == "NPC-4412"

    def test_movements_are_grouped_by_schedule_and_kind(self, depot):
        ship(depot, packs=5)
        found = extracts.controlled_movements(
            organization=depot["wholesale"],
            start=TODAY - timedelta(days=1),
            end=TODAY,
        )
        kinds = {row["kind"] for row in found["movements"]}
        assert MovementKind.WHOLESALE_DISPATCH in kinds
        assert MovementKind.PURCHASE_RECEIPT in kinds

    def test_quota_usage_reports_what_is_left(self, depot):
        quota(depot, limit_base=10_000)
        ship(depot, packs=10)
        usage = extracts.quota_usage(organization=depot["wholesale"])
        assert usage[0]["used_base"] == 1_000
        assert usage[0]["remaining_base"] == 9_000

    def test_the_bundle_carries_every_section(self, depot):
        found = extracts.bundle(
            organization=depot["wholesale"],
            start=TODAY - timedelta(days=30),
            end=TODAY,
        )
        assert set(found) >= {
            "organization", "register", "movements", "transfers", "quotas"
        }

    def test_the_extract_is_bounded_by_its_period(self, depot):
        ship(depot, packs=5)
        earlier = extracts.controlled_transfers(
            organization=depot["wholesale"],
            start=TODAY - timedelta(days=300),
            end=TODAY - timedelta(days=200),
        )
        assert earlier["transfers"] == []
