"""Commercial alert boundaries.

79, 80, 81 percent. The warning threshold is configuration, so the
boundary is the contract.
"""

from datetime import date, timedelta

import pytest

from commerce import checks, invoicing, services
from commerce.models import InvoiceKind, InvoiceStatus, TradingRelationship
from core.alerts import AcknowledgementRequired, Severity
from core.exceptions import LicenceInvalid
from core.models import (
    Branch,
    LicenceKind,
    LicenceStatus,
    PremisesLicence,
    User,
)
from core.quantity import Quantity
from inventory import services as inventory
from inventory.models import MovementKind
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db

LIMIT = 1_000_000


def licence(org, kind, *, days=365):
    branch, _ = Branch.objects.get_or_create(
        organization=org, code="MAIN", defaults={"name": "Main"}
    )
    return PremisesLicence.objects.create(
        organization=org,
        branch=branch,
        kind=kind,
        number=f"RFDA-{kind}-{org.name[:4]}",
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

    product = make_product(wholesale, "Amoxicillin 500mg")
    depot = make_location(wholesale, "Depot", "DEP")
    store = make_location(retail, "Store", "MAIN")
    batch = make_batch(wholesale, product, number="A1", unit_cost_base=280)
    inventory.post_movement(
        organization=wholesale,
        location=depot,
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        quantity=Quantity(100, uom(product, "PACK")),
    )
    relationship = TradingRelationship.objects.create(
        organization=wholesale, customer=retail, is_verified=True, credit_limit=LIMIT
    )
    listing = services.publish_listing(
        organization=wholesale,
        product=product,
        price=10_000,
        price_uom=uom(product, "PACK"),
        offered_base=Quantity(100, uom(product, "PACK")).base_value,
        performed_by=seller,
    )
    return {
        "wholesale": wholesale, "retail": retail, "seller": seller, "buyer": buyer,
        "owner": owner, "listing": listing, "store": store, "depot": depot,
        "relationship": relationship, "batch": batch, "product": product,
    }


def order_for(trade, *, packs):
    order = services.start_order(
        organization=trade["retail"],
        supplier=trade["wholesale"],
        deliver_to=trade["store"],
        performed_by=trade["buyer"],
    )
    services.add_order_line(order=order, listing=trade["listing"], quantity=packs)
    services.request_approval(order=order, performed_by=trade["buyer"])
    return services.submit_order(order=order, performed_by=trade["owner"])


class TestCreditBoundary:
    """The limit is 1,000,000 and a pack is 10,000."""

    def test_seventy_nine_percent_is_silent(self, trade):
        found = checks.credit(
            supplier=trade["wholesale"], customer=trade["retail"], pending=790_000
        )
        assert found == []

    def test_exactly_eighty_percent_warns(self, trade):
        found = checks.credit(
            supplier=trade["wholesale"], customer=trade["retail"], pending=800_000
        )
        assert [alert.code for alert in found] == ["CREDIT_LIMIT_NEAR"]
        assert found[0].severity == Severity.WARNING

    def test_at_the_limit_exactly_still_only_warns(self, trade):
        """Over is a refusal; at is not over."""
        found = checks.credit(
            supplier=trade["wholesale"], customer=trade["retail"], pending=LIMIT
        )
        assert [alert.code for alert in found] == ["CREDIT_LIMIT_NEAR"]

    def test_one_franc_over_blocks(self, trade):
        found = checks.credit(
            supplier=trade["wholesale"], customer=trade["retail"], pending=LIMIT + 1
        )
        assert [alert.code for alert in found] == ["CREDIT_LIMIT_EXCEEDED"]
        assert found[0].severity == Severity.CRITICAL

    def test_no_limit_means_the_check_does_not_apply(self, trade):
        trade["relationship"].credit_limit = 0
        trade["relationship"].save(update_fields=["credit_limit"])
        assert (
            checks.credit(
                supplier=trade["wholesale"], customer=trade["retail"], pending=9_000_000
            )
            == []
        )


class TestConfirmEnforcesCredit:
    def test_approaching_the_limit_refuses_until_acknowledged(self, trade):
        order = order_for(trade, packs=85)  # 850,000 — 85%
        with pytest.raises(AcknowledgementRequired) as raised:
            services.confirm_order(order=order, performed_by=trade["seller"])
        assert raised.value.meta["alerts"][0]["code"] == "CREDIT_LIMIT_NEAR"

    def test_acknowledging_lets_it_through_and_records_why(self, trade):
        from core.alerts import AlertAcknowledgement

        order = order_for(trade, packs=85)
        services.confirm_order(
            order=order,
            performed_by=trade["seller"],
            acknowledged=["CREDIT_LIMIT_NEAR"],
            reason="Long-standing customer.",
        )
        order.refresh_from_db()
        assert order.status == "CONFIRMED"
        record = AlertAcknowledgement.objects.get(code="CREDIT_LIMIT_NEAR")
        assert record.reason == "Long-standing customer."

    def test_a_lapsed_buyer_licence_blocks_supply(self, trade):
        """The supplier is separately responsible for who it ships to."""
        PremisesLicence.objects.filter(organization=trade["retail"]).update(
            expiry=date.today() - timedelta(days=1)
        )
        order = order_for(trade, packs=10)
        with pytest.raises(LicenceInvalid):
            services.confirm_order(order=order, performed_by=trade["seller"])


class TestBelowCost:
    def test_below_the_batch_cost_warns(self, trade):
        found = checks.below_cost(
            product=trade["product"], price_base=200, batch=trade["batch"]
        )
        assert [alert.code for alert in found] == ["SALE_BELOW_COST"]

    def test_at_cost_is_silent(self, trade):
        assert (
            checks.below_cost(
                product=trade["product"], price_base=280, batch=trade["batch"]
            )
            == []
        )

    def test_it_is_this_batch_not_an_average(self, trade):
        """FEFO says which batch is leaving, so the check is exact."""
        dearer = make_batch(
            trade["wholesale"], trade["product"], number="A2", unit_cost_base=400
        )
        assert checks.below_cost(
            product=trade["product"], price_base=300, batch=dearer
        )
        assert not checks.below_cost(
            product=trade["product"], price_base=300, batch=trade["batch"]
        )

    def test_a_batch_with_no_recorded_cost_is_silent(self, trade):
        free = make_batch(
            trade["wholesale"], trade["product"], number="A3", unit_cost_base=0
        )
        assert (
            checks.below_cost(product=trade["product"], price_base=1, batch=free) == []
        )


class TestReceivablesOverdue:
    def overdue_invoice(self, trade, *, days, total=100_000):
        order = order_for(trade, packs=10)
        services.confirm_order(order=order, performed_by=trade["seller"])
        invoice = invoicing.build_invoice(
            order=order, kind=InvoiceKind.TAX, performed_by=trade["seller"]
        )
        invoicing.issue_invoice(invoice=invoice, performed_by=trade["seller"])
        invoice.total = total
        invoice.due_on = date.today() - timedelta(days=days)
        invoice.save(update_fields=["total", "due_on"])
        return invoice

    def test_inside_the_terms_is_silent(self, trade):
        self.overdue_invoice(trade, days=29)
        assert checks.receivables_overdue(supplier=trade["wholesale"]) == []

    def test_past_the_terms_warns(self, trade):
        self.overdue_invoice(trade, days=31)
        found = checks.receivables_overdue(supplier=trade["wholesale"])
        assert [alert.code for alert in found] == ["RECEIVABLE_OVERDUE"]
        assert found[0].meta["days"] == 31

    def test_one_alert_per_customer_not_per_invoice(self, trade):
        """Nine overdue invoices is one conversation, not nine banners."""
        self.overdue_invoice(trade, days=45)
        self.overdue_invoice(trade, days=60)
        found = checks.receivables_overdue(supplier=trade["wholesale"])
        assert len(found) == 1
        assert found[0].meta["days"] == 60
        assert found[0].meta["amount"] == 200_000

    def test_a_paid_invoice_drops_out(self, trade):
        invoice = self.overdue_invoice(trade, days=45)
        invoicing.record_payment(
            invoice=invoice, amount=invoice.total, performed_by=trade["seller"]
        )
        assert checks.receivables_overdue(supplier=trade["wholesale"]) == []
