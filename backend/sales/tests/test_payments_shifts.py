"""Payments, shifts, day end and fiscal submission.

The pending-payment path is the one worth testing hardest: mobile money
resolves out of band, and a mock that settled instantly would hide every
bug in it.
"""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from catalog.models import LegalStatus
from core.models import Branch, User
from core.quantity import Quantity
from fiscal.models import FiscalRecord, FiscalStatus
from fiscal.services import FiscalIntegrationService, exceptions_for
from inventory import services as inventory
from inventory.models import MovementKind
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom
from sales import payments, services, shifts
from sales.models import PaymentMethod, PaymentStatus, SaleStatus, Shift, ShiftStatus, Till

pytestmark = pytest.mark.django_db


@pytest.fixture
def counter():
    org = make_org()
    branch = Branch.objects.create(organization=org, name="Kigali Main", code="KGL")
    location = make_location(org)
    till = Till.objects.create(organization=org, branch=branch, name="Till 02", code="T02")
    cashier = User.objects.create_user(username="cashier", password="x", organization=org)

    product = make_product(org, "Paracetamol 500mg", legal_status=LegalStatus.OTC)
    batch = make_batch(org, product)
    inventory.post_movement(
        organization=org,
        location=location,
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        quantity=Quantity(10, uom(product, "PACK")),
    )
    return {
        "org": org,
        "branch": branch,
        "location": location,
        "till": till,
        "cashier": cashier,
        "product": product,
    }


def sell(counter, *, shift=None, quantity=10, unit_price=100):
    """A completed OTC sale of `quantity` units."""
    sale = services.start_sale(
        organization=counter["org"],
        branch=counter["branch"],
        location=counter["location"],
        cashier=counter["cashier"],
        till=counter["till"],
        shift=shift,
    )
    services.add_line(
        sale=sale,
        product=counter["product"],
        quantity=quantity,
        uom=uom(counter["product"], "UNIT"),
        unit_price=unit_price,
    )
    return services.complete_sale(sale=sale, performed_by=counter["cashier"])


# --------------------------------------------------------------------------


class TestCash:
    def test_settles_immediately(self, counter):
        sale = sell(counter)
        payment = payments.take_payment(
            sale=sale, method=PaymentMethod.CASH, amount=sale.total,
            performed_by=counter["cashier"],
        )
        assert payment.status == PaymentStatus.CONFIRMED
        assert payments.amount_outstanding(sale) == 0

    def test_overpayment_refused(self, counter):
        sale = sell(counter)
        with pytest.raises(payments.Overpayment):
            payments.take_payment(
                sale=sale, method=PaymentMethod.CASH, amount=sale.total + 1,
                performed_by=counter["cashier"],
            )

    def test_split_across_two_methods(self, counter):
        sale = sell(counter)
        payments.take_payment(
            sale=sale, method=PaymentMethod.CASH, amount=400,
            performed_by=counter["cashier"],
        )
        payments.take_payment(
            sale=sale, method=PaymentMethod.CASH, amount=600,
            performed_by=counter["cashier"],
        )
        assert payments.amount_outstanding(sale) == 0


class TestMobileMoney:
    """Request-to-pay resolves out of band. PENDING is a real state."""

    def test_starts_pending_not_settled(self, counter):
        sale = sell(counter)
        payment = payments.take_payment(
            sale=sale, method=PaymentMethod.MOBILE_MONEY, amount=sale.total,
            performed_by=counter["cashier"], phone="0788000000",
        )
        assert payment.status == PaymentStatus.PENDING
        assert payment.provider_reference.startswith("MOCK-")

    def test_sale_is_pending_payment_while_money_is_in_flight(self, counter):
        sale = sell(counter)
        payments.take_payment(
            sale=sale, method=PaymentMethod.MOBILE_MONEY, amount=sale.total,
            performed_by=counter["cashier"],
        )
        sale.refresh_from_db()
        assert sale.status == SaleStatus.PENDING_PAYMENT

    def test_callback_confirms_and_completes(self, counter):
        sale = sell(counter)
        payment = payments.take_payment(
            sale=sale, method=PaymentMethod.MOBILE_MONEY, amount=sale.total,
            performed_by=counter["cashier"],
        )
        payments.resolve_payment(payment=payment, confirmed=True, provider_reference="MTN-1")

        sale.refresh_from_db()
        assert sale.status == SaleStatus.COMPLETED
        assert payments.amount_settled(sale) == sale.total

    def test_callback_is_idempotent(self, counter):
        """Providers retry their callbacks. A repeat must not double-count."""
        sale = sell(counter)
        payment = payments.take_payment(
            sale=sale, method=PaymentMethod.MOBILE_MONEY, amount=sale.total,
            performed_by=counter["cashier"],
        )
        payments.resolve_payment(payment=payment, confirmed=True)
        payments.resolve_payment(payment=payment, confirmed=False)  # late, contradictory

        payment.refresh_from_db()
        assert payment.status == PaymentStatus.CONFIRMED
        assert payments.amount_settled(sale) == sale.total

    def test_failed_callback_leaves_the_sale_unsettled(self, counter):
        sale = sell(counter)
        payment = payments.take_payment(
            sale=sale, method=PaymentMethod.MOBILE_MONEY, amount=sale.total,
            performed_by=counter["cashier"],
        )
        payments.resolve_payment(payment=payment, confirmed=False)

        sale.refresh_from_db()
        assert sale.status == SaleStatus.PENDING_PAYMENT
        assert payments.amount_outstanding(sale) == sale.total

    def test_stale_request_times_out(self, counter):
        """Otherwise the sale sits pending for ever and the day never closes."""
        sale = sell(counter)
        payment = payments.take_payment(
            sale=sale, method=PaymentMethod.MOBILE_MONEY, amount=sale.total,
            performed_by=counter["cashier"],
        )
        payment.requested_at = timezone.now() - timedelta(hours=1)
        payment.save(update_fields=["requested_at"])

        expired = payments.expire_stale_payments(organization=counter["org"])
        payment.refresh_from_db()

        assert expired == 1
        assert payment.status == PaymentStatus.TIMED_OUT

    def test_a_fresh_request_is_not_timed_out(self, counter):
        sale = sell(counter)
        payments.take_payment(
            sale=sale, method=PaymentMethod.MOBILE_MONEY, amount=sale.total,
            performed_by=counter["cashier"],
        )
        assert payments.expire_stale_payments(organization=counter["org"]) == 0


class TestShift:
    def test_one_open_shift_per_till(self, counter):
        shifts.open_shift(till=counter["till"], opened_by=counter["cashier"])
        with pytest.raises(shifts.ShiftAlreadyOpen):
            shifts.open_shift(till=counter["till"], opened_by=counter["cashier"])

    def test_x_report_reads_without_closing(self, counter):
        shift = shifts.open_shift(
            till=counter["till"], opened_by=counter["cashier"], opening_float=5000
        )
        sale = sell(counter, shift=shift)
        payments.take_payment(
            sale=sale, method=PaymentMethod.CASH, amount=sale.total,
            performed_by=counter["cashier"],
        )

        summary = shifts.report(shift)
        shift.refresh_from_db()

        assert summary.transactions == 1
        assert summary.sales_total == 1000
        assert summary.expected_cash == 6000
        assert shift.status == ShiftStatus.OPEN

    def test_pending_money_is_not_in_the_drawer(self, counter):
        shift = shifts.open_shift(till=counter["till"], opened_by=counter["cashier"])
        sale = sell(counter, shift=shift)
        payments.take_payment(
            sale=sale, method=PaymentMethod.MOBILE_MONEY, amount=sale.total,
            performed_by=counter["cashier"],
        )

        summary = shifts.report(shift)
        assert summary.expected_cash == 0
        assert summary.pending_payments == 1

    def test_close_balanced(self, counter):
        shift = shifts.open_shift(
            till=counter["till"], opened_by=counter["cashier"], opening_float=5000
        )
        sale = sell(counter, shift=shift)
        payments.take_payment(
            sale=sale, method=PaymentMethod.CASH, amount=sale.total,
            performed_by=counter["cashier"],
        )

        summary = shifts.close_shift(
            shift=shift, counted_cash=6000, closed_by=counter["cashier"]
        )
        shift.refresh_from_db()

        assert summary.variance == 0
        assert summary.is_balanced
        assert shift.status == ShiftStatus.CLOSED

    def test_large_variance_needs_a_reason(self, counter):
        shift = shifts.open_shift(
            till=counter["till"], opened_by=counter["cashier"], opening_float=5000
        )
        with pytest.raises(shifts.VarianceUnexplained):
            shifts.close_shift(
                shift=shift, counted_cash=1000, closed_by=counter["cashier"]
            )

    def test_large_variance_closes_with_a_reason(self, counter):
        shift = shifts.open_shift(
            till=counter["till"], opened_by=counter["cashier"], opening_float=5000
        )
        summary = shifts.close_shift(
            shift=shift,
            counted_cash=1000,
            closed_by=counter["cashier"],
            variance_reason="Float taken to the bank mid-shift",
        )
        assert summary.variance == -4000

    def test_small_variance_closes_without_one(self, counter):
        shift = shifts.open_shift(
            till=counter["till"], opened_by=counter["cashier"], opening_float=5000
        )
        summary = shifts.close_shift(
            shift=shift, counted_cash=4900, closed_by=counter["cashier"]
        )
        assert summary.variance == -100

    def test_refuses_to_close_over_a_pending_payment(self, counter):
        """A pending request-to-pay produces a variance that is not a
        counting error, and would be chased as one."""
        shift = shifts.open_shift(till=counter["till"], opened_by=counter["cashier"])
        sale = sell(counter, shift=shift)
        payments.take_payment(
            sale=sale, method=PaymentMethod.MOBILE_MONEY, amount=sale.total,
            performed_by=counter["cashier"],
        )
        with pytest.raises(shifts.SalesStillPending):
            shifts.close_shift(shift=shift, counted_cash=0, closed_by=counter["cashier"])

    def test_cannot_close_twice(self, counter):
        shift = shifts.open_shift(till=counter["till"], opened_by=counter["cashier"])
        shifts.close_shift(shift=shift, counted_cash=0, closed_by=counter["cashier"])
        with pytest.raises(shifts.ShiftNotOpen):
            shifts.close_shift(shift=shift, counted_cash=0, closed_by=counter["cashier"])

    def test_breakdown_by_method(self, counter):
        shift = shifts.open_shift(till=counter["till"], opened_by=counter["cashier"])
        a = sell(counter, shift=shift)
        payments.take_payment(
            sale=a, method=PaymentMethod.CASH, amount=a.total,
            performed_by=counter["cashier"],
        )
        b = sell(counter, shift=shift)
        momo = payments.take_payment(
            sale=b, method=PaymentMethod.MOBILE_MONEY, amount=b.total,
            performed_by=counter["cashier"],
        )
        payments.resolve_payment(payment=momo, confirmed=True)

        summary = shifts.report(shift)
        assert summary.by_method == {"CASH": 1000, "MOBILE_MONEY": 1000}
        assert summary.transactions == 2


class TestFiscal:
    def test_posted_sale_is_accepted(self, counter):
        sale = sell(counter)
        record = FiscalIntegrationService().submit(sale)

        assert record.status == FiscalStatus.ACCEPTED
        assert record.receipt_number.startswith("MOCK-")
        assert record.attempts == 1

    def test_payload_carries_per_line_tax(self, counter):
        """A pharmacy basket is mixed-treatment; a single total is wrong."""
        sale = sell(counter)
        record = FiscalIntegrationService().submit(sale)
        assert record.payload["lines"][0]["tax_treatment"] == "EXEMPT"

    def test_accepted_record_is_immutable(self, counter):
        sale = sell(counter)
        record = FiscalIntegrationService().submit(sale)
        record.receipt_number = "TAMPERED"
        with pytest.raises(RuntimeError, match="immutable"):
            record.save()

    def test_resubmitting_an_accepted_sale_is_a_no_op(self, counter):
        sale = sell(counter)
        service = FiscalIntegrationService()
        first = service.submit(sale)
        second = service.submit(sale)
        assert first.id == second.id
        assert second.attempts == 1

    def test_a_failure_lands_in_the_exception_queue(self, counter):
        """Never silently unfiscalized — that is the RRA finding."""
        from fiscal.services import FiscalBackend

        class Broken(FiscalBackend):
            code = "broken"

            def submit(self, sale):
                raise ConnectionError("VSDC unreachable")

        sale = sell(counter)
        record = FiscalIntegrationService(backend=Broken()).submit(sale)

        assert record.status == FiscalStatus.FAILED
        assert record.error_code == "ConnectionError"
        assert record.needs_attention
        assert list(exceptions_for(counter["org"])) == [record]

    def test_retry_after_a_failure_succeeds(self, counter):
        from fiscal.services import FiscalBackend

        class Broken(FiscalBackend):
            code = "broken"

            def submit(self, sale):
                raise ConnectionError("VSDC unreachable")

        sale = sell(counter)
        failed = FiscalIntegrationService(backend=Broken()).submit(sale)
        recovered = FiscalIntegrationService().retry(failed)

        assert recovered.status == FiscalStatus.ACCEPTED
        assert recovered.attempts == 2
        assert list(exceptions_for(counter["org"])) == []

    def test_draft_sale_is_not_fiscalized(self, counter):
        draft = services.start_sale(
            organization=counter["org"],
            branch=counter["branch"],
            location=counter["location"],
            cashier=counter["cashier"],
        )
        with pytest.raises(RuntimeError, match="posted sale"):
            FiscalIntegrationService().submit(draft)

    def test_vsdc_backend_is_explicitly_pending_verification(self):
        """The boundary is real, not hypothetical — but V1 is unanswered."""
        from fiscal.services import VsdcBackend

        with pytest.raises(NotImplementedError, match="V1"):
            VsdcBackend().submit(None)


class TestSettlement:
    """A sale is revenue only once the money is actually settled."""

    def test_an_unpaid_sale_is_not_completed(self, counter):
        """Goods left the counter, but nothing was tendered. Day end must
        not count this as revenue."""
        sale = sell(counter)
        assert sale.status == SaleStatus.PENDING_PAYMENT

    def test_paying_in_full_completes_it(self, counter):
        sale = sell(counter)
        payments.take_payment(
            sale=sale, method=PaymentMethod.CASH, amount=sale.total,
            performed_by=counter["cashier"],
        )
        sale.refresh_from_db()
        assert sale.status == SaleStatus.COMPLETED

    def test_partial_payment_leaves_it_pending(self, counter):
        sale = sell(counter)
        payments.take_payment(
            sale=sale, method=PaymentMethod.CASH, amount=sale.total - 1,
            performed_by=counter["cashier"],
        )
        sale.refresh_from_db()
        assert sale.status == SaleStatus.PENDING_PAYMENT
        assert payments.amount_outstanding(sale) == 1

    def test_stock_leaves_even_when_unpaid(self, counter):
        """Completion moves stock; settlement is a separate question."""
        before = inventory.balance_for(
            organization=counter["org"], product=counter["product"]
        )
        sell(counter, quantity=10)
        after = inventory.balance_for(
            organization=counter["org"], product=counter["product"]
        )
        assert before - after == 10
