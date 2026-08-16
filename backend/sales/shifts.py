"""Tills, shifts and day end.

This is the screen that replaces the notebook. The pharmacist reviews an
exception rather than reconstructing a day, because the system already
recorded every sale as it happened.

X report reads the shift without closing it. Z report closes it.

See docs/19-screens.md §13.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from core.exceptions import DomainError
from core.models import User
from sales.models import (
    Payment,
    PaymentMethod,
    PaymentStatus,
    Sale,
    SaleStatus,
    Shift,
    ShiftStatus,
    Till,
)

#: Variance beyond this needs a reason before the day can close.
VARIANCE_THRESHOLD = 1000  # RWF


class ShiftAlreadyOpen(DomainError):
    default_code = "shift_already_open"
    default_detail = "This till already has an open shift."


class ShiftNotOpen(DomainError):
    default_code = "shift_not_open"
    default_detail = "This shift is already closed."


class VarianceUnexplained(DomainError):
    default_code = "variance_unexplained"
    default_detail = "A cash variance this size needs a reason."


class SalesStillPending(DomainError):
    default_code = "sales_pending"
    default_detail = "Resolve pending payments before closing."


@dataclass
class DayEnd:
    """What the pharmacist sees at close. Facts, no commentary."""

    sales_total: int = 0
    transactions: int = 0
    items_sold: int = 0
    discounts: int = 0
    tax_total: int = 0
    by_method: dict[str, int] = field(default_factory=dict)
    expected_cash: int = 0
    counted_cash: int | None = None
    variance: int | None = None
    pending_payments: int = 0

    @property
    def is_balanced(self) -> bool:
        return self.variance == 0


@transaction.atomic
def open_shift(*, till: Till, opened_by: User, opening_float: int = 0) -> Shift:
    if Shift.objects.filter(till=till, status=ShiftStatus.OPEN).exists():
        raise ShiftAlreadyOpen()
    return Shift.objects.create(
        organization=till.organization,
        till=till,
        opened_by=opened_by,
        opening_float=opening_float,
        created_by=opened_by,
    )


def report(shift: Shift) -> DayEnd:
    """The X report. Reads without closing.

    Counts only settled money as cash in the drawer; a pending mobile
    money request is not in the till.
    """
    sales = Sale.objects.filter(shift=shift).exclude(status=SaleStatus.VOIDED)

    totals = sales.aggregate(
        total=Sum("total"),
        tax=Sum("tax_total"),
        discount=Sum("discount_total"),
        count=Count("id"),
    )
    items = (
        sales.aggregate(items=Sum("lines__quantity"))["items"] or 0
    )

    by_method: dict[str, int] = {}
    settled = Payment.objects.filter(
        sale__shift=shift, status=PaymentStatus.CONFIRMED
    ).values("method").annotate(total=Sum("amount"))
    for row in settled:
        by_method[row["method"]] = row["total"]

    pending = Payment.objects.filter(
        sale__shift=shift, status=PaymentStatus.PENDING
    ).count()

    cash = by_method.get(PaymentMethod.CASH, 0)

    return DayEnd(
        sales_total=totals["total"] or 0,
        transactions=totals["count"] or 0,
        items_sold=items,
        discounts=totals["discount"] or 0,
        tax_total=totals["tax"] or 0,
        by_method=by_method,
        expected_cash=shift.opening_float + cash,
        counted_cash=shift.counted_cash,
        variance=shift.variance,
        pending_payments=pending,
    )


@transaction.atomic
def close_shift(
    *,
    shift: Shift,
    counted_cash: int,
    closed_by: User,
    variance_reason: str = "",
    allow_pending: bool = False,
) -> DayEnd:
    """The Z report. Closes the shift and records the variance.

    Refuses while payments are still in flight unless explicitly allowed —
    closing over a pending request-to-pay produces a variance that is not
    a counting error and will be chased as one.
    """
    if shift.status != ShiftStatus.OPEN:
        raise ShiftNotOpen()

    summary = report(shift)

    if summary.pending_payments and not allow_pending:
        raise SalesStillPending(
            f"{summary.pending_payments} payment(s) still pending.",
            meta={"pending": summary.pending_payments},
        )

    variance = counted_cash - summary.expected_cash
    if abs(variance) > VARIANCE_THRESHOLD and not variance_reason.strip():
        raise VarianceUnexplained(
            f"Counted {counted_cash}, expected {summary.expected_cash}.",
            meta={"variance": variance, "threshold": VARIANCE_THRESHOLD},
        )

    shift.counted_cash = counted_cash
    shift.variance = variance
    shift.variance_reason = variance_reason
    shift.closed_by = closed_by
    shift.closed_at = timezone.now()
    shift.status = ShiftStatus.CLOSED
    shift.modified_by = closed_by
    shift.save(
        update_fields=[
            "counted_cash",
            "variance",
            "variance_reason",
            "closed_by",
            "closed_at",
            "status",
            "modified_by",
            "modified_at",
        ]
    )

    summary.counted_cash = counted_cash
    summary.variance = variance
    return summary
