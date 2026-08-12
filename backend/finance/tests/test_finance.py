"""Period reports, expenses, write-offs and credit notes.

Two tests carry most of the weight:

* `test_a_backdated_credit_note_corrects_the_period_it_belongs_to` — the
  entire argument for computing rather than storing periods.
* `test_cogs_is_the_actual_batch_not_an_average` — the entire argument
  for cost living on the batch.
"""

from datetime import date, timedelta

import pytest

from catalog.models import LegalStatus, TaxTreatment
from commerce import invoicing, services
from commerce.models import InvoiceKind, TradingRelationship
from core.exceptions import DomainError
from core.models import (
    AuditEvent,
    Branch,
    LicenceKind,
    LicenceStatus,
    PremisesLicence,
    User,
)
from core.quantity import Quantity
from documents import services as documents
from documents.models import DocumentKind
from finance import reports, services as finance
from finance.models import Expense, ExpenseCategory, WriteOff, WriteOffReason
from inventory import services as inventory
from inventory.models import MovementKind
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db

TODAY = date.today()
MONTH_AGO = TODAY - timedelta(days=30)
QUARTER_AGO = TODAY - timedelta(days=90)


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

    product = make_product(wholesale, "Amoxicillin 500mg", tax=TaxTreatment.EXEMPT)
    store = make_location(retail, "Store", "MAIN")
    warehouse = make_location(wholesale, "Depot", "DEP")

    TradingRelationship.objects.create(
        organization=wholesale, customer=retail, is_verified=True, credit_limit=100_000_000
    )
    return {
        "wholesale": wholesale, "retail": retail, "seller": seller, "buyer": buyer,
        "owner": owner, "product": product, "store": store, "warehouse": warehouse,
    }


def stocked(depot, *, batch_number, unit_cost, packs=20):
    batch = make_batch(
        depot["wholesale"], depot["product"], number=batch_number, unit_cost_base=unit_cost
    )
    inventory.post_movement(
        organization=depot["wholesale"],
        location=depot["warehouse"],
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        quantity=Quantity(packs, uom(depot["product"], "PACK")),
    )
    return batch


def sell(depot, *, packs, price, listing=None):
    """Order → confirm → dispatch. Returns the shipment."""
    # Offer everything held: a depot cannot publish more than it holds,
    # and these tests care about what leaves, not about the allocation.
    on_hand = inventory.balance_for(
        organization=depot["wholesale"], product=depot["product"]
    )
    listing = listing or services.publish_listing(
        organization=depot["wholesale"],
        product=depot["product"],
        price=price,
        price_uom=uom(depot["product"], "PACK"),
        offered_base=on_hand,
        performed_by=depot["seller"],
    )
    order = services.start_order(
        organization=depot["retail"],
        supplier=depot["wholesale"],
        deliver_to=depot["store"],
        performed_by=depot["buyer"],
    )
    services.add_order_line(order=order, listing=listing, quantity=packs)
    services.request_approval(order=order, performed_by=depot["buyer"])
    services.submit_order(order=order, performed_by=depot["owner"])
    services.confirm_order(order=order, performed_by=depot["seller"])
    shipment = services.dispatch_order(
        order=order, from_location=depot["warehouse"], performed_by=depot["seller"]
    )
    return order, shipment, listing


class TestExpenses:
    def test_seeding_gives_somewhere_to_put_costs(self, depot):
        created = finance.seed_categories(depot["wholesale"])
        assert len(created) == 9
        assert ExpenseCategory.objects.filter(code="COLD_CHAIN").exists()

    def test_seeding_twice_does_not_duplicate(self, depot):
        finance.seed_categories(depot["wholesale"])
        finance.seed_categories(depot["wholesale"])
        assert ExpenseCategory.objects.filter(organization=depot["wholesale"]).count() == 9

    def test_an_expense_is_dated_to_when_it_was_incurred(self, depot):
        """Not to when somebody did their filing."""
        finance.seed_categories(depot["wholesale"])
        rent = ExpenseCategory.objects.get(organization=depot["wholesale"], code="RENT")
        expense = finance.record_expense(
            organization=depot["wholesale"],
            category=rent,
            amount=400_000,
            incurred_on=QUARTER_AGO,
            performed_by=depot["seller"],
        )
        assert expense.incurred_on == QUARTER_AGO

    def test_a_negative_expense_is_refused(self, depot):
        finance.seed_categories(depot["wholesale"])
        rent = ExpenseCategory.objects.get(organization=depot["wholesale"], code="RENT")
        with pytest.raises(DomainError):
            finance.record_expense(
                organization=depot["wholesale"], category=rent, amount=-1
            )

    def test_recording_is_audited(self, depot):
        finance.seed_categories(depot["wholesale"])
        rent = ExpenseCategory.objects.get(organization=depot["wholesale"], code="RENT")
        finance.record_expense(
            organization=depot["wholesale"],
            category=rent,
            amount=1_000,
            performed_by=depot["seller"],
        )
        assert AuditEvent.objects.filter(action="finance.expense.recorded").exists()

    def test_only_operating_expenses_reach_the_result(self, depot):
        """A one-off equipment purchase is not this month's trading."""
        finance.seed_categories(depot["wholesale"])
        for code, amount in [("RENT", 400_000), ("EQUIPMENT", 900_000)]:
            finance.record_expense(
                organization=depot["wholesale"],
                category=ExpenseCategory.objects.get(
                    organization=depot["wholesale"], code=code
                ),
                amount=amount,
            )
        rows = reports.expenses(
            organization=depot["wholesale"], start=MONTH_AGO, end=TODAY
        )
        assert [row.code for row in rows] == ["RENT"]


class TestWriteOff:
    def test_it_leaves_the_ledger_through_post_movement(self, depot):
        batch = stocked(depot, batch_number="A1", unit_cost=280)
        before = inventory.balance_for(
            organization=depot["wholesale"], product=depot["product"]
        )
        finance.write_off(
            organization=depot["wholesale"],
            batch=batch,
            location=depot["warehouse"],
            quantity=Quantity(2, uom(depot["product"], "PACK")),
            reason=WriteOffReason.EXPIRY,
            performed_by=depot["seller"],
            witness_name="Marie U.",
            witness_role="Responsible pharmacist",
        )
        after = inventory.balance_for(
            organization=depot["wholesale"], product=depot["product"]
        )
        assert before - after == 200

    def test_the_value_is_this_batch_cost(self, depot):
        batch = stocked(depot, batch_number="A1", unit_cost=280)
        record = finance.write_off(
            organization=depot["wholesale"],
            batch=batch,
            location=depot["warehouse"],
            quantity=Quantity(2, uom(depot["product"], "PACK")),
            reason=WriteOffReason.EXPIRY,
            performed_by=depot["seller"],
        )
        assert record.value == 200 * 280

    def test_a_certificate_is_issued_with_the_witness(self, depot):
        batch = stocked(depot, batch_number="A1", unit_cost=280)
        record = finance.write_off(
            organization=depot["wholesale"],
            batch=batch,
            location=depot["warehouse"],
            quantity=Quantity(1, uom(depot["product"], "PACK")),
            reason=WriteOffReason.EXPIRY,
            performed_by=depot["seller"],
            witness_name="Marie U.",
            witness_role="Responsible pharmacist",
        )
        document = documents.latest(subject=record, kind=DocumentKind.WRITE_OFF)
        assert document is not None
        assert "Marie U." in document.html
        assert record.number.startswith("WO-")

    def test_an_unknown_reason_is_refused(self, depot):
        batch = stocked(depot, batch_number="A1", unit_cost=280)
        with pytest.raises(DomainError):
            finance.write_off(
                organization=depot["wholesale"],
                batch=batch,
                location=depot["warehouse"],
                quantity=Quantity(1, uom(depot["product"], "PACK")),
                reason="BECAUSE",
                performed_by=depot["seller"],
            )


class TestDepotPeriodReport:
    def test_revenue_is_net_of_tax(self, depot):
        """VAT collected is not the depot's money."""
        stocked(depot, batch_number="A1", unit_cost=280)
        order, _, _ = sell(depot, packs=10, price=10_000)
        invoice = invoicing.build_invoice(order=order, performed_by=depot["seller"])
        invoicing.issue_invoice(invoice=invoice, performed_by=depot["seller"])

        report = reports.period_report(
            organization=depot["wholesale"],
            start=MONTH_AGO,
            end=TODAY,
            tier=reports.DEPOT,
        )
        assert report.revenue == invoice.subtotal

    def test_cogs_is_the_batch_that_actually_left(self, depot):
        stocked(depot, batch_number="A1", unit_cost=280)
        sell(depot, packs=10, price=10_000)
        report = reports.period_report(
            organization=depot["wholesale"],
            start=MONTH_AGO,
            end=TODAY,
            tier=reports.DEPOT,
        )
        assert report.cogs == 1_000 * 280

    def test_cogs_is_the_actual_batch_not_an_average(self, depot):
        """FEFO says which batch left, so the cost is exact.

        A cheap batch expiring soon and a dear one expiring later. FEFO
        ships the cheap one; an average would charge the mean and get
        both this month's margin and next month's wrong.
        """
        make_batch(
            depot["wholesale"], depot["product"], number="CHEAP",
            unit_cost_base=200, expires_in_days=60,
        )
        make_batch(
            depot["wholesale"], depot["product"], number="DEAR",
            unit_cost_base=600, expires_in_days=400,
        )
        for number in ("CHEAP", "DEAR"):
            from inventory.models import Batch

            inventory.post_movement(
                organization=depot["wholesale"],
                location=depot["warehouse"],
                batch=Batch.objects.get(
                    organization=depot["wholesale"], batch_number=number
                ),
                kind=MovementKind.PURCHASE_RECEIPT,
                quantity=Quantity(10, uom(depot["product"], "PACK")),
            )

        sell(depot, packs=10, price=10_000)  # 1,000 base units
        report = reports.period_report(
            organization=depot["wholesale"],
            start=MONTH_AGO,
            end=TODAY,
            tier=reports.DEPOT,
        )
        assert report.cogs == 1_000 * 200  # not 1,000 × 400

    def test_gross_profit_reconciles(self, depot):
        stocked(depot, batch_number="A1", unit_cost=280)
        order, _, _ = sell(depot, packs=10, price=10_000)
        invoice = invoicing.build_invoice(order=order, performed_by=depot["seller"])
        invoicing.issue_invoice(invoice=invoice, performed_by=depot["seller"])

        report = reports.period_report(
            organization=depot["wholesale"],
            start=MONTH_AGO,
            end=TODAY,
            tier=reports.DEPOT,
        )
        assert report.gross_profit == report.revenue - report.cogs

    def test_margin_is_basis_points_not_a_float(self, depot):
        # 10 packs at 10,000 is 100,000 revenue; 1,000 base units at 25
        # is 25,000 cost. 75,000 of 100,000 is 7,500 basis points.
        stocked(depot, batch_number="A1", unit_cost=25)
        order, _, _ = sell(depot, packs=10, price=10_000)
        invoice = invoicing.build_invoice(order=order, performed_by=depot["seller"])
        invoicing.issue_invoice(invoice=invoice, performed_by=depot["seller"])

        report = reports.period_report(
            organization=depot["wholesale"],
            start=MONTH_AGO,
            end=TODAY,
            tier=reports.DEPOT,
        )
        assert isinstance(report.gross_margin_bp, int)
        assert report.gross_margin_bp == 7_500  # 75%

    def test_an_empty_period_has_no_margin_rather_than_zero(self, depot):
        """No revenue and break-even are different facts."""
        report = reports.period_report(
            organization=depot["wholesale"],
            start=MONTH_AGO,
            end=TODAY,
            tier=reports.DEPOT,
        )
        assert report.gross_margin_bp is None
        assert report.roi_bp is None

    def test_the_period_bounds_are_respected(self, depot):
        stocked(depot, batch_number="A1", unit_cost=280)
        order, _, _ = sell(depot, packs=10, price=10_000)
        invoice = invoicing.build_invoice(order=order, performed_by=depot["seller"])
        invoicing.issue_invoice(invoice=invoice, performed_by=depot["seller"])

        earlier = reports.period_report(
            organization=depot["wholesale"],
            start=TODAY - timedelta(days=300),
            end=TODAY - timedelta(days=200),
            tier=reports.DEPOT,
        )
        assert earlier.revenue == 0
        assert earlier.cogs == 0

    def test_a_backwards_period_is_refused(self, depot):
        with pytest.raises(DomainError):
            reports.period_report(
                organization=depot["wholesale"], start=TODAY, end=MONTH_AGO
            )

    def test_write_offs_come_off_the_operating_result_not_cogs(self, depot):
        """Destroyed goods were never sold. They are not cost of goods sold."""
        batch = stocked(depot, batch_number="A1", unit_cost=280)
        order, _, _ = sell(depot, packs=10, price=10_000)
        invoice = invoicing.build_invoice(order=order, performed_by=depot["seller"])
        invoicing.issue_invoice(invoice=invoice, performed_by=depot["seller"])
        finance.write_off(
            organization=depot["wholesale"],
            batch=batch,
            location=depot["warehouse"],
            quantity=Quantity(2, uom(depot["product"], "PACK")),
            reason=WriteOffReason.EXPIRY,
            performed_by=depot["seller"],
        )
        report = reports.period_report(
            organization=depot["wholesale"],
            start=MONTH_AGO,
            end=TODAY,
            tier=reports.DEPOT,
        )
        assert report.write_offs == 200 * 280
        assert report.cogs == 1_000 * 280
        assert report.estimated_operating_result == report.gross_profit - report.write_offs


class TestNoNetProfit:
    """CLAUDE.md is explicit, and the report obeys it in its field names."""

    def test_the_report_never_says_net_profit(self, depot):
        report = reports.period_report(
            organization=depot["wholesale"], start=MONTH_AGO, end=TODAY
        )
        assert "net_profit" not in report.as_dict()
        assert "estimated_operating_result" in report.as_dict()

    def test_the_estimate_states_what_it_excludes(self, depot):
        report = reports.period_report(
            organization=depot["wholesale"], start=MONTH_AGO, end=TODAY
        )
        assert "depreciation" in report.estimated_basis
        assert "tax" in report.estimated_basis

    def test_the_phrase_appears_nowhere_in_the_module(self):
        from pathlib import Path

        source = Path(reports.__file__).read_text(encoding="utf-8")
        # The docstring explains why it is absent; that mention is the
        # only permitted one.
        body = source.split('"""', 2)[-1]
        assert "net_profit" not in body


class TestCreditNotes:
    def test_a_credit_note_reduces_revenue(self, depot):
        stocked(depot, batch_number="A1", unit_cost=280)
        order, _, _ = sell(depot, packs=10, price=10_000)
        invoice = invoicing.build_invoice(order=order, performed_by=depot["seller"])
        invoicing.issue_invoice(invoice=invoice, performed_by=depot["seller"])

        before = reports.depot_revenue(
            organization=depot["wholesale"], start=MONTH_AGO, end=TODAY
        )
        invoicing.raise_credit_note(
            against=invoice,
            amount=30_000,
            reason="Short delivery.",
            performed_by=depot["seller"],
        )
        after = reports.depot_revenue(
            organization=depot["wholesale"], start=MONTH_AGO, end=TODAY
        )
        assert after == before - 30_000

    def test_a_backdated_credit_note_corrects_the_period_it_belongs_to(self, depot):
        """The whole argument for computing periods rather than storing them.

        A credit agreed today for a delivery 60 days ago belongs to that
        earlier period. A stored total would leave the old period
        overstated for ever and the current one understated to match.
        """
        stocked(depot, batch_number="A1", unit_cost=280)
        order, _, _ = sell(depot, packs=10, price=10_000)
        invoice = invoicing.build_invoice(order=order, performed_by=depot["seller"])
        invoicing.issue_invoice(invoice=invoice, performed_by=depot["seller"])
        invoice.issued_on = TODAY - timedelta(days=60)
        invoice.save(update_fields=["issued_on"])

        old_period = (TODAY - timedelta(days=70), TODAY - timedelta(days=50))
        before = reports.depot_revenue(
            organization=depot["wholesale"], start=old_period[0], end=old_period[1]
        )

        invoicing.raise_credit_note(
            against=invoice,
            amount=30_000,
            reason="Short delivery, agreed late.",
            performed_by=depot["seller"],
            issued_on=TODAY - timedelta(days=60),
        )

        after = reports.depot_revenue(
            organization=depot["wholesale"], start=old_period[0], end=old_period[1]
        )
        current = reports.depot_revenue(
            organization=depot["wholesale"],
            start=TODAY - timedelta(days=7),
            end=TODAY,
        )
        assert after == before - 30_000
        assert current == 0

    def test_crediting_more_than_was_invoiced_is_refused(self, depot):
        stocked(depot, batch_number="A1", unit_cost=280)
        order, _, _ = sell(depot, packs=10, price=10_000)
        invoice = invoicing.build_invoice(order=order, performed_by=depot["seller"])
        invoicing.issue_invoice(invoice=invoice, performed_by=depot["seller"])

        with pytest.raises(DomainError):
            invoicing.raise_credit_note(
                against=invoice,
                amount=invoice.total + 1,
                reason="Too much.",
                performed_by=depot["seller"],
            )

    def test_two_credits_cannot_exceed_the_invoice_between_them(self, depot):
        stocked(depot, batch_number="A1", unit_cost=280)
        order, _, _ = sell(depot, packs=10, price=10_000)
        invoice = invoicing.build_invoice(order=order, performed_by=depot["seller"])
        invoicing.issue_invoice(invoice=invoice, performed_by=depot["seller"])

        invoicing.raise_credit_note(
            against=invoice, amount=60_000, reason="One.", performed_by=depot["seller"]
        )
        with pytest.raises(DomainError):
            invoicing.raise_credit_note(
                against=invoice,
                amount=invoice.total - 60_000 + 1,
                reason="Two.",
                performed_by=depot["seller"],
            )

    def test_a_credit_note_needs_a_reason(self, depot):
        stocked(depot, batch_number="A1", unit_cost=280)
        order, _, _ = sell(depot, packs=10, price=10_000)
        invoice = invoicing.build_invoice(order=order, performed_by=depot["seller"])
        invoicing.issue_invoice(invoice=invoice, performed_by=depot["seller"])

        with pytest.raises(DomainError):
            invoicing.raise_credit_note(
                against=invoice, amount=1_000, reason="  ", performed_by=depot["seller"]
            )

    def test_it_frees_the_credit_limit(self, depot):
        stocked(depot, batch_number="A1", unit_cost=280)
        order, _, _ = sell(depot, packs=10, price=10_000)
        invoice = invoicing.build_invoice(order=order, performed_by=depot["seller"])
        invoicing.issue_invoice(invoice=invoice, performed_by=depot["seller"])

        before = invoicing.outstanding_for(
            supplier=depot["wholesale"], customer=depot["retail"]
        )
        invoicing.raise_credit_note(
            against=invoice, amount=50_000, reason="Returned.", performed_by=depot["seller"]
        )
        after = invoicing.outstanding_for(
            supplier=depot["wholesale"], customer=depot["retail"]
        )
        assert after == before - 50_000

    def test_a_document_is_issued_naming_what_it_credits(self, depot):
        stocked(depot, batch_number="A1", unit_cost=280)
        order, _, _ = sell(depot, packs=10, price=10_000)
        invoice = invoicing.build_invoice(order=order, performed_by=depot["seller"])
        invoicing.issue_invoice(invoice=invoice, performed_by=depot["seller"])

        note = invoicing.raise_credit_note(
            against=invoice,
            amount=50_000,
            reason="Short delivery.",
            performed_by=depot["seller"],
        )
        document = documents.latest(subject=note, kind=DocumentKind.CREDIT_NOTE)
        assert invoice.number in document.html
        assert "Short delivery." in document.html
        assert note.number.startswith("CN-")


class TestReceivablesAgeing:
    def issued(self, depot, *, days_overdue):
        stocked(depot, batch_number=f"B{days_overdue}", unit_cost=280)
        order, _, _ = sell(depot, packs=5, price=10_000)
        invoice = invoicing.build_invoice(order=order, performed_by=depot["seller"])
        invoicing.issue_invoice(invoice=invoice, performed_by=depot["seller"])
        invoice.due_on = TODAY - timedelta(days=days_overdue)
        invoice.save(update_fields=["due_on"])
        return invoice

    def test_buckets_sum_to_the_total(self, depot):
        self.issued(depot, days_overdue=10)
        self.issued(depot, days_overdue=45)
        self.issued(depot, days_overdue=200)

        ageing = reports.receivables_ageing(supplier=depot["wholesale"])
        assert sum(ageing["buckets"].values()) == ageing["total"]

    def test_each_invoice_lands_in_the_right_bucket(self, depot):
        self.issued(depot, days_overdue=10)
        self.issued(depot, days_overdue=45)
        self.issued(depot, days_overdue=75)
        self.issued(depot, days_overdue=200)

        ageing = reports.receivables_ageing(supplier=depot["wholesale"])
        assert all(amount > 0 for amount in ageing["buckets"].values())

    def test_it_breaks_down_by_customer(self, depot):
        """The bucket says how bad; the customer says who to ring."""
        self.issued(depot, days_overdue=45)
        ageing = reports.receivables_ageing(supplier=depot["wholesale"])
        assert ageing["customers"][0]["customer"] == "Kigali Care"

    def test_a_paid_invoice_drops_out(self, depot):
        invoice = self.issued(depot, days_overdue=45)
        invoicing.record_payment(
            invoice=invoice, amount=invoice.total, performed_by=depot["seller"]
        )
        assert reports.receivables_ageing(supplier=depot["wholesale"])["total"] == 0


class TestCapitalInvested:
    def test_landed_cost_is_part_of_the_investment(self, depot):
        """A depot's capital is not the invoice."""
        receipt = services.start_receipt(
            organization=depot["wholesale"],
            location=depot["warehouse"],
            performed_by=depot["seller"],
        )
        receipt.freight = 100_000
        receipt.save(update_fields=["freight"])
        services.add_receipt_line(
            receipt=receipt,
            product=depot["product"],
            uom=uom(depot["product"], "PACK"),
            received=10,
            batch_number="IMP-1",
            expiry_date=TODAY + timedelta(days=400),
            unit_cost_base=300,
        )
        services.post_receipt(receipt=receipt, performed_by=depot["seller"])

        invested = reports.capital_invested(
            organization=depot["wholesale"], start=MONTH_AGO, end=TODAY
        )
        assert invested == 300 * 1_000 + 100_000


class TestDashboardSeries:
    """The four charts docs/28 §12.5 specifies."""

    def test_the_trend_has_one_row_per_month(self, depot):
        rows = reports.investment_against_revenue(
            organization=depot["wholesale"],
            start=TODAY - timedelta(days=70),
            end=TODAY,
            tier=reports.DEPOT,
        )
        assert 3 <= len(rows) <= 4
        assert set(rows[0]) == {"period", "invested", "revenue"}

    def test_both_trend_series_are_money(self, depot):
        """Which is why they share one axis rather than getting two."""
        stocked(depot, batch_number="A1", unit_cost=280)
        order, _, _ = sell(depot, packs=10, price=10_000)
        invoice = invoicing.build_invoice(order=order, performed_by=depot["seller"])
        invoicing.issue_invoice(invoice=invoice, performed_by=depot["seller"])

        rows = reports.investment_against_revenue(
            organization=depot["wholesale"],
            start=MONTH_AGO,
            end=TODAY,
            tier=reports.DEPOT,
        )
        assert all(isinstance(row["invested"], int) for row in rows)
        assert all(isinstance(row["revenue"], int) for row in rows)

    def test_a_partial_month_is_clipped_not_rounded_out(self, depot):
        """Reporting the whole month would look like a collapse in trade."""
        rows = reports.investment_against_revenue(
            organization=depot["wholesale"],
            start=TODAY.replace(day=1),
            end=TODAY.replace(day=1),
            tier=reports.DEPOT,
        )
        assert len(rows) == 1

    def test_inventory_health_bands_by_expiry_runway(self, depot):
        make_batch(
            depot["wholesale"], depot["product"], number="SOON",
            unit_cost_base=100, expires_in_days=30,
        )
        make_batch(
            depot["wholesale"], depot["product"], number="MID",
            unit_cost_base=100, expires_in_days=150,
        )
        make_batch(
            depot["wholesale"], depot["product"], number="FAR",
            unit_cost_base=100, expires_in_days=500,
        )
        from inventory.models import Batch

        for number in ("SOON", "MID", "FAR"):
            inventory.post_movement(
                organization=depot["wholesale"],
                location=depot["warehouse"],
                batch=Batch.objects.get(
                    organization=depot["wholesale"], batch_number=number
                ),
                kind=MovementKind.PURCHASE_RECEIPT,
                quantity=Quantity(1, uom(depot["product"], "PACK")),
            )

        health = reports.inventory_health(organization=depot["wholesale"])[0]
        assert health["expiring"] == 100 * 100
        assert health["slow"] == 100 * 100
        assert health["stable"] == 100 * 100

    def test_revenue_by_category_folds_the_tail_into_other(self, depot):
        """Thirteen categories against three dark slots is a form problem."""
        rows = reports.revenue_by_category(
            organization=depot["wholesale"],
            start=MONTH_AGO,
            end=TODAY,
            tier=reports.DEPOT,
            top=3,
        )
        assert len(rows) <= 4
        if len(rows) == 4:
            assert rows[-1]["category"] == "Other"

    def test_sales_against_collections_separates_the_two(self, depot):
        """A depot can trade itself out of cash while revenue rises."""
        stocked(depot, batch_number="A1", unit_cost=280)
        order, _, _ = sell(depot, packs=10, price=10_000)
        invoice = invoicing.build_invoice(order=order, performed_by=depot["seller"])
        invoicing.issue_invoice(invoice=invoice, performed_by=depot["seller"])

        rows = reports.sales_against_collections(
            organization=depot["wholesale"], start=MONTH_AGO, end=TODAY
        )
        this_month = rows[-1]
        assert this_month["invoiced"] == invoice.total
        assert this_month["collected"] == 0

        invoicing.record_payment(
            invoice=invoice, amount=invoice.total, performed_by=depot["seller"]
        )
        rows = reports.sales_against_collections(
            organization=depot["wholesale"], start=MONTH_AGO, end=TODAY
        )
        assert rows[-1]["collected"] == invoice.total

    def test_the_dashboard_returns_every_panel(self, depot):
        payload = reports.dashboard(
            organization=depot["wholesale"],
            start=MONTH_AGO,
            end=TODAY,
            tier=reports.DEPOT,
        )
        assert set(payload) == {
            "report", "trend", "inventory_health", "revenue_by_category", "cash"
        }
