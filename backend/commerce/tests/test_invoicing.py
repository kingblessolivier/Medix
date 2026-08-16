"""Invoicing, payment terms and credit.

The credit tests matter most. A limit that is checked against historic
debt only is not a limit — a pharmacy already at it places one more order
every time, because the order being approved is never in the sum.
"""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from catalog.models import LegalStatus, TaxTreatment
from commerce import invoicing, services
from commerce.models import (
    Invoice,
    InvoiceKind,
    InvoiceStatus,
    TradingRelationship,
)
from core.models import Branch, LicenceKind, LicenceStatus, PremisesLicence, User
from core.quantity import Quantity
from inventory import services as inventory
from inventory.models import MovementKind
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom
from sales.models import TaxRule

pytestmark = pytest.mark.django_db


def licence(org, kind, number=None):
    branch, _ = Branch.objects.get_or_create(
        organization=org, code="MAIN", defaults={"name": "Main"}
    )
    return PremisesLicence.objects.create(
        organization=org,
        branch=branch,
        kind=kind,
        number=number or f"RFDA-{kind}-{org.name[:4]}",
        issued_on=date.today() - timedelta(days=400),
        expiry=date.today() + timedelta(days=365),
        status=LicenceStatus.ACTIVE,
    )


@pytest.fixture
def trade():
    depot = make_org("ABC Wholesale", kind=LicenceKind.WHOLESALE_PHARMACY)
    shop = make_org("Kigali Care", kind=LicenceKind.RETAIL_PHARMACY)
    licence(depot, LicenceKind.WHOLESALE_PHARMACY)
    licence(shop, LicenceKind.RETAIL_PHARMACY)

    seller = User.objects.create_user(username="jean", password="x", organization=depot)
    buyer = User.objects.create_user(username="marie", password="x", organization=shop)
    owner = User.objects.create_user(username="claudine", password="x", organization=shop)

    # Standard-rated so tax actually appears; medicines are exempt.
    product = make_product(depot, "Surgical gloves", legal_status=LegalStatus.OTC,
                           tax=TaxTreatment.STANDARD)
    TaxRule.objects.create(
        organization=depot,
        treatment=TaxTreatment.STANDARD,
        rate_basis_points=1800,
        effective_from=date.today() - timedelta(days=365),
    )

    depot_store = make_location(depot, "Depot", "DEP")
    shop_store = make_location(shop, "Shop", "MAIN")
    batch = make_batch(depot, product, number="GLV-1")
    inventory.post_movement(
        organization=depot,
        location=depot_store,
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        # Deep enough that credit, not allocation, is the binding
        # constraint in the credit tests below.
        quantity=Quantity(400, uom(product, "PACK")),
    )

    relationship = TradingRelationship.objects.create(
        organization=depot,
        customer=shop,
        is_verified=True,
        verified_at=timezone.now(),
        credit_limit=1_000_000,
        payment_terms_days=30,
    )

    return {
        "depot": depot, "shop": shop, "seller": seller, "buyer": buyer, "owner": owner,
        "product": product, "depot_store": depot_store, "shop_store": shop_store,
        "relationship": relationship,
    }


def make_order(trade, *, packs=10, price=20_000):
    listing = services.publish_listing(
        organization=trade["depot"],
        product=trade["product"],
        price=price,
        price_uom=uom(trade["product"], "PACK"),
        moq=1,
        offered_base=40_000,
    )
    order = services.start_order(
        organization=trade["shop"],
        supplier=trade["depot"],
        deliver_to=trade["shop_store"],
        performed_by=trade["buyer"],
    )
    services.add_order_line(order=order, listing=listing, quantity=packs)
    services.request_approval(order=order, performed_by=trade["buyer"])
    services.submit_order(order=order, performed_by=trade["owner"])
    return order


class TestPaymentTerms:
    def test_terms_are_frozen_onto_the_order(self, trade):
        """Renegotiating next month must not restate an agreed order."""
        order = make_order(trade)
        assert order.payment_terms_days == 30

        trade["relationship"].payment_terms_days = 60
        trade["relationship"].save(update_fields=["payment_terms_days"])
        order.refresh_from_db()
        assert order.payment_terms_days == 30

    def test_due_date_follows_the_terms(self, trade):
        order = make_order(trade)
        services.confirm_order(order=order, performed_by=trade["seller"])
        invoice = invoicing.build_invoice(order=order, performed_by=trade["seller"])
        invoicing.issue_invoice(invoice=invoice, performed_by=trade["seller"])

        assert invoice.due_on == invoice.issued_on + timedelta(days=30)

    def test_a_proforma_has_no_due_date(self, trade):
        """It asks for payment before goods move; it is not a debt."""
        order = make_order(trade)
        services.confirm_order(order=order, performed_by=trade["seller"])
        proforma = invoicing.build_invoice(
            order=order, performed_by=trade["seller"], kind=InvoiceKind.PROFORMA
        )
        invoicing.issue_invoice(invoice=proforma, performed_by=trade["seller"])

        assert proforma.number.startswith("PI-")
        assert proforma.due_on is None

    def test_a_proforma_is_not_a_receivable(self, trade):
        order = make_order(trade)
        services.confirm_order(order=order, performed_by=trade["seller"])
        proforma = invoicing.build_invoice(
            order=order, performed_by=trade["seller"], kind=InvoiceKind.PROFORMA
        )
        invoicing.issue_invoice(invoice=proforma, performed_by=trade["seller"])

        assert invoicing.outstanding_for(
            supplier=trade["depot"], customer=trade["shop"]
        ) == 0


class TestInvoiceTax:
    def test_tax_is_computed_from_the_dated_rule(self, trade):
        order = make_order(trade, packs=10, price=20_000)
        services.confirm_order(order=order, performed_by=trade["seller"])
        invoice = invoicing.build_invoice(order=order, performed_by=trade["seller"])

        assert invoice.subtotal == 200_000
        assert invoice.tax_total == 36_000  # 18%
        assert invoice.total == 236_000

    def test_the_rate_is_frozen_onto_the_line(self, trade):
        """An invoice must read the same in five years.

        Changing the rule afterwards must not restate a document already
        issued.
        """
        order = make_order(trade)
        services.confirm_order(order=order, performed_by=trade["seller"])
        invoice = invoicing.build_invoice(order=order, performed_by=trade["seller"])
        invoicing.issue_invoice(invoice=invoice, performed_by=trade["seller"])

        TaxRule.objects.filter(organization=trade["depot"]).update(rate_basis_points=2500)
        invoice.refresh_from_db()

        assert invoice.lines.get().tax_rate_basis_points == 1800
        assert invoice.tax_total == 36_000

    def test_exempt_products_carry_no_tax(self, trade):
        medicine = make_product(
            trade["depot"], "Amoxicillin 500mg", tax=TaxTreatment.EXEMPT
        )
        batch = make_batch(trade["depot"], medicine, number="AMX-9")
        inventory.post_movement(
            organization=trade["depot"], location=trade["depot_store"], batch=batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(20, uom(medicine, "PACK")),
        )
        listing = services.publish_listing(
            organization=trade["depot"], product=medicine, price=28_000,
            price_uom=uom(medicine, "PACK"), moq=1, offered_base=2000,
        )
        order = services.start_order(
            organization=trade["shop"], supplier=trade["depot"],
            deliver_to=trade["shop_store"], performed_by=trade["buyer"],
        )
        services.add_order_line(order=order, listing=listing, quantity=2)
        services.request_approval(order=order, performed_by=trade["buyer"])
        services.submit_order(order=order, performed_by=trade["owner"])
        services.confirm_order(order=order, performed_by=trade["seller"])

        invoice = invoicing.build_invoice(order=order, performed_by=trade["seller"])
        assert invoice.tax_total == 0
        assert invoice.lines.get().tax_treatment == TaxTreatment.EXEMPT


class TestPayment:
    def _issued(self, trade):
        order = make_order(trade, packs=10, price=20_000)
        services.confirm_order(order=order, performed_by=trade["seller"])
        invoice = invoicing.build_invoice(order=order, performed_by=trade["seller"])
        return invoicing.issue_invoice(invoice=invoice, performed_by=trade["seller"])

    def test_partial_payment_is_normal(self, trade):
        invoice = self._issued(trade)
        invoicing.record_payment(
            invoice=invoice, amount=100_000, performed_by=trade["seller"]
        )
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.PART_PAID
        assert invoice.outstanding == 136_000

    def test_paying_the_balance_closes_it(self, trade):
        invoice = self._issued(trade)
        invoicing.record_payment(invoice=invoice, amount=100_000, performed_by=trade["seller"])
        invoicing.record_payment(invoice=invoice, amount=136_000, performed_by=trade["seller"])
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.PAID
        assert invoice.outstanding == 0

    def test_overpayment_is_refused(self, trade):
        """Money beyond the debt is a credit note, not a payment."""
        invoice = self._issued(trade)
        with pytest.raises(Exception, match="more than"):
            invoicing.record_payment(
                invoice=invoice, amount=300_000, performed_by=trade["seller"]
            )

    def test_settled_reads_the_database_not_a_cache(self, trade):
        """A prefetched invoice must not report a stale balance.

        Fetched exactly as a viewset would, with payments prefetched.
        """
        invoice = self._issued(trade)
        invoicing.record_payment(invoice=invoice, amount=236_000, performed_by=trade["seller"])

        prefetched = Invoice.objects.prefetch_related("payments").get(pk=invoice.pk)
        list(prefetched.payments.all())
        assert prefetched.outstanding == 0


class TestCredit:
    def test_order_within_the_limit_is_approved(self, trade):
        order = make_order(trade, packs=10, price=20_000)  # 200,000
        services.confirm_order(order=order, performed_by=trade["seller"])
        assert order.status == "CONFIRMED"

    def test_order_beyond_the_limit_is_refused(self, trade):
        """The limit is 1,000,000 and the order is 1,200,000."""
        order = make_order(trade, packs=30, price=40_000)
        with pytest.raises(invoicing.CreditLimitExceeded):
            services.confirm_order(order=order, performed_by=trade["seller"])

    def test_the_order_being_approved_counts_towards_the_limit(self, trade):
        """The rule this whole check exists for.

        900,000 already owed and a 200,000 order is 1,100,000 against a
        1,000,000 limit. Counting only the historic debt would approve it,
        and would approve the next one too, for ever.
        """
        first = make_order(trade, packs=45, price=20_000)  # 900,000
        # 90% of the limit, so the depot has to accept the warning before
        # this one goes through — see docs/29 §6.
        services.confirm_order(
            order=first,
            performed_by=trade["seller"],
            acknowledged=["CREDIT_LIMIT_NEAR"],
        )
        invoice = invoicing.build_invoice(order=first, performed_by=trade["seller"])
        invoice.tax_total = 0
        invoice.total = 900_000
        invoice.save(update_fields=["tax_total", "total"])
        invoicing.issue_invoice(invoice=invoice, performed_by=trade["seller"])

        second = make_order(trade, packs=10, price=20_000)  # 200,000
        with pytest.raises(invoicing.CreditLimitExceeded):
            services.confirm_order(order=second, performed_by=trade["seller"])

    def test_paying_frees_the_limit_again(self, trade):
        first = make_order(trade, packs=45, price=20_000)
        services.confirm_order(
            order=first,
            performed_by=trade["seller"],
            acknowledged=["CREDIT_LIMIT_NEAR"],
        )
        invoice = invoicing.build_invoice(order=first, performed_by=trade["seller"])
        invoice.tax_total = 0
        invoice.total = 900_000
        invoice.save(update_fields=["tax_total", "total"])
        invoicing.issue_invoice(invoice=invoice, performed_by=trade["seller"])
        invoicing.record_payment(invoice=invoice, amount=900_000, performed_by=trade["seller"])

        second = make_order(trade, packs=10, price=20_000)
        services.confirm_order(order=second, performed_by=trade["seller"])
        assert second.status == "CONFIRMED"

    def test_no_limit_means_no_credit_check(self, trade):
        """A depot with no limit recorded trades on immediate payment."""
        trade["relationship"].credit_limit = 0
        trade["relationship"].save(update_fields=["credit_limit"])

        order = make_order(trade, packs=45, price=40_000)
        services.confirm_order(order=order, performed_by=trade["seller"])
        assert order.status == "CONFIRMED"

    def test_eighty_percent_is_a_warning_not_a_refusal(self, trade):
        position = invoicing.credit_position(
            supplier=trade["depot"], customer=trade["shop"], pending=850_000
        )
        assert position["near_limit"] is True
        assert position["available"] == 150_000


class TestAgeing:
    def _overdue(self, trade, days):
        order = make_order(trade, packs=5, price=20_000)
        services.confirm_order(order=order, performed_by=trade["seller"])
        invoice = invoicing.build_invoice(order=order, performed_by=trade["seller"])
        invoicing.issue_invoice(invoice=invoice, performed_by=trade["seller"])
        invoice.due_on = date.today() - timedelta(days=days)
        invoice.save(update_fields=["due_on"])
        return invoice

    def test_buckets_by_days_overdue(self, trade):
        self._overdue(trade, 5)
        self._overdue(trade, 45)
        self._overdue(trade, 200)

        report = invoicing.receivables_ageing(supplier=trade["depot"])
        assert report["totals"]["0-30"] == 118_000
        assert report["totals"]["31-60"] == 118_000
        assert report["totals"]["91+"] == 118_000
        assert report["total"] == 354_000

    def test_a_paid_invoice_leaves_the_report(self, trade):
        invoice = self._overdue(trade, 90)
        invoicing.record_payment(
            invoice=invoice, amount=invoice.total, performed_by=trade["seller"]
        )
        report = invoicing.receivables_ageing(supplier=trade["depot"])
        assert report["total"] == 0
