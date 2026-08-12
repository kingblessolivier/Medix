"""What did I put in, what did I get back, over this period.

Both tiers answer the same question and the shape of the answer differs:
a depot lives on volume at a thin margin, a pharmacy on a fatter margin
over fewer units. So the figures are the same and the sources are not.

**Everything here is computed for an arbitrary date range.** Nothing is
stored as a period. A stored total is only true until someone backdates a
credit note, and then it is quietly wrong with nothing to say so — and
fixed periods make "what did I earn between the 3rd and the 17th"
unanswerable. Materialise as a cache later if a query gets slow, keyed so
it can be invalidated; never as the source. See docs/28 §12.1.

**COGS is exact, not averaged.** Cost lives on the batch, FEFO records
which batch left, and both `SaleLine` and `ShipmentLine` hold it. So the
cost of a sale is the cost of the actual goods.

**The words "net profit" do not appear** — not in a field, a label or a
variable. Net profit depends on depreciation, accruals and a tax position
this system does not see. A pharmacist who reads "net profit: 65,000" and
files a return on it has been misled by us. See docs/28 §12.3.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

from django.db.models import F, Sum

from core.models import Organization

DEPOT = "DEPOT"
RETAIL = "RETAIL"

#: What the estimated operating result does *not* account for. Printed
#: alongside the figure, always, so the number is never read as more than
#: it is.
ESTIMATE_BASIS = (
    "Gross profit less recorded operating expenses. Excludes depreciation, "
    "accruals, tax and anything not entered as an expense."
)


@dataclass(frozen=True)
class CategoryTotal:
    code: str
    name: str
    amount: int


@dataclass(frozen=True)
class PeriodReport:
    """Every figure in minor units, every ratio in basis points.

    Basis points rather than a float percentage: 33.33% is not
    representable in binary floating point, and a margin that drifts in
    the fourth decimal across two screens invites the question of which
    one is lying.
    """

    organization_id: str
    tier: str
    start: date
    end: date
    currency: str

    capital_invested: int
    revenue: int
    cogs: int
    gross_profit: int
    gross_margin_bp: int

    expenses_total: int
    expenses: list[CategoryTotal]
    estimated_operating_result: int
    estimated_basis: str

    write_offs: int
    stock_at_risk: int
    roi_bp: int | None

    #: Retail only — the split matters because insurance revenue is
    #: recognised before the money arrives.
    cash_revenue: int = 0
    insurance_revenue: int = 0

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["start"] = self.start.isoformat()
        payload["end"] = self.end.isoformat()
        return payload


def _basis_points(part: int, whole: int) -> int | None:
    """Integer ratio, or None when the denominator is zero.

    None, not zero. A margin of zero and no revenue at all are different
    facts, and returning 0 for both makes an empty period look like a
    break-even one.
    """
    if whole == 0:
        return None
    return part * 10_000 // whole


# --------------------------------------------------------------------------
# The components
# --------------------------------------------------------------------------


def capital_invested(*, organization: Organization, start: date, end: date) -> int:
    """What was paid to put stock on the shelf, landed cost included.

    A depot's capital is not the invoice: freight, duty and clearing are
    real money spent acquiring the goods. `post_receipt` apportions them
    into the batch, and `landed_cost_share` records each line's part.
    """
    from commerce.models import GoodsReceiptLine, GoodsReceiptStatus

    lines = GoodsReceiptLine.objects.filter(
        receipt__organization=organization,
        receipt__status=GoodsReceiptStatus.POSTED,
        receipt__received_on__gte=start,
        receipt__received_on__lte=end,
        accepted__gt=0,
    ).select_related("receipt", "uom")

    total = 0
    for line in lines:
        accepted_base = line.accepted * line.uom.factor_to_base
        rate = line.receipt.fx_rate_scaled
        cost = (
            line.unit_cost_base
            if line.receipt.invoice_currency == "RWF"
            else line.unit_cost_base * rate // 10_000
        )
        total += cost * accepted_base + line.landed_cost_share
    return total


def depot_revenue(*, organization: Organization, start: date, end: date) -> int:
    """Wholesale sales, net of tax and net of credit notes.

    **Ex-tax.** VAT collected is not the depot's money; counting it as
    revenue inflates both the top line and the margin.

    Credit notes are subtracted at their own issue date, which is why
    `raise_credit_note` lets that date be set: a January credit for a
    November delivery belongs to November.
    """
    from commerce.models import Invoice, InvoiceKind, InvoiceStatus

    settled = [InvoiceStatus.ISSUED, InvoiceStatus.PART_PAID, InvoiceStatus.PAID]
    invoiced = (
        Invoice.objects.filter(
            organization=organization,
            kind=InvoiceKind.TAX,
            status__in=settled,
            issued_on__gte=start,
            issued_on__lte=end,
        ).aggregate(total=Sum("subtotal"))["total"]
        or 0
    )
    credited = (
        Invoice.objects.filter(
            organization=organization,
            kind=InvoiceKind.CREDIT_NOTE,
            status__in=settled,
            issued_on__gte=start,
            issued_on__lte=end,
        ).aggregate(total=Sum("subtotal"))["total"]
        or 0
    )
    return invoiced - credited


def depot_cogs(*, organization: Organization, start: date, end: date) -> int:
    """The batch cost of what actually left the depot.

    Against the invoice date rather than the dispatch date would be
    tidier, but the goods left when they left — matching cost to the
    shipment is what makes the margin describe the trade rather than the
    paperwork.
    """
    from commerce.models import ShipmentLine, ShipmentStatus

    return (
        ShipmentLine.objects.filter(
            shipment__organization=organization,
            shipment__status=ShipmentStatus.DISPATCHED,
            shipment__dispatched_at__date__gte=start,
            shipment__dispatched_at__date__lte=end,
        ).aggregate(total=Sum(F("quantity_base") * F("batch__unit_cost_base")))["total"]
        or 0
    )


def retail_revenue(*, organization: Organization, start: date, end: date) -> dict:
    """Counter sales, ex-tax, split by how they were paid.

    The split is not cosmetic. Insurance revenue is recognised when the
    prescription is dispensed and collected weeks later, if the claim is
    not rejected — a pharmacy reading one total cannot tell whether it has
    had a good month or merely a busy one.
    """
    from sales.models import Payment, PaymentMethod, PaymentStatus, Sale, SaleLine, SaleStatus

    completed = Sale.objects.filter(
        organization=organization,
        status=SaleStatus.COMPLETED,
        occurred_at__date__gte=start,
        occurred_at__date__lte=end,
    )
    net = (
        SaleLine.objects.filter(sale__in=completed).aggregate(
            total=Sum(F("line_subtotal") - F("discount"))
        )["total"]
        or 0
    )

    insurance_paid = (
        Payment.objects.filter(
            sale__in=completed,
            method=PaymentMethod.INSURANCE,
            status__in=[PaymentStatus.CONFIRMED, PaymentStatus.PENDING],
        ).aggregate(total=Sum("amount"))["total"]
        or 0
    )
    gross = completed.aggregate(total=Sum("total"))["total"] or 0

    # Payments are gross of tax while revenue is net, so the insurance
    # share is applied as a proportion rather than subtracted directly.
    insurance_share = (net * insurance_paid // gross) if gross else 0
    return {
        "total": net,
        "insurance": insurance_share,
        "cash": net - insurance_share,
    }


def retail_cogs(*, organization: Organization, start: date, end: date) -> int:
    """Exact per line. `SaleLine` holds the batch cost FEFO gave it."""
    from sales.models import SaleLine, SaleStatus

    return (
        SaleLine.objects.filter(
            sale__organization=organization,
            sale__status=SaleStatus.COMPLETED,
            sale__occurred_at__date__gte=start,
            sale__occurred_at__date__lte=end,
        ).aggregate(total=Sum(F("quantity_base") * F("unit_cost_base")))["total"]
        or 0
    )


def expenses(*, organization: Organization, start: date, end: date) -> list[CategoryTotal]:
    from finance.models import Expense

    rows = (
        Expense.objects.filter(
            organization=organization,
            incurred_on__gte=start,
            incurred_on__lte=end,
            category__is_operating=True,
        )
        .values("category__code", "category__name")
        .annotate(amount=Sum("amount"))
        .order_by("-amount")
    )
    return [
        CategoryTotal(
            code=row["category__code"], name=row["category__name"], amount=row["amount"]
        )
        for row in rows
    ]


def write_offs(*, organization: Organization, start: date, end: date) -> int:
    """Value destroyed. One of the three quiet leakages.

    Expired stock was paid for, sat on the shelf, and left without a sale.
    Leaving it out of the operating result makes the result flatter itself.
    """
    from finance.models import WriteOff

    return (
        WriteOff.objects.filter(
            organization=organization,
            written_off_on__gte=start,
            written_off_on__lte=end,
        ).aggregate(total=Sum("value"))["total"]
        or 0
    )


def stock_at_risk(*, organization: Organization, as_of: date | None = None, days: int = 90) -> int:
    """Capital sitting in stock that is about to become a write-off.

    The number a clearance decision is made against: it is still an asset
    today and it is on a timer.
    """
    from datetime import timedelta

    from django.utils import timezone

    from inventory.models import StockBalance, StockStatus

    as_of = as_of or timezone.localdate()
    return (
        StockBalance.objects.filter(
            organization=organization,
            status=StockStatus.AVAILABLE,
            expiry_date__gt=as_of,
            expiry_date__lte=as_of + timedelta(days=days),
        ).aggregate(total=Sum(F("quantity_base") * F("batch__unit_cost_base")))["total"]
        or 0
    )


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


def period_report(
    *,
    organization: Organization,
    start: date,
    end: date,
    tier: str = RETAIL,
    currency: str = "RWF",
) -> PeriodReport:
    """Invested against gained, for any range the caller asks for."""
    if end < start:
        from core.exceptions import DomainError

        raise DomainError("The period ends before it starts.", code="invalid_period")

    invested = capital_invested(organization=organization, start=start, end=end)

    if tier == DEPOT:
        revenue = depot_revenue(organization=organization, start=start, end=end)
        cogs = depot_cogs(organization=organization, start=start, end=end)
        cash = insurance = 0
    else:
        split = retail_revenue(organization=organization, start=start, end=end)
        revenue = split["total"]
        cash = split["cash"]
        insurance = split["insurance"]
        cogs = retail_cogs(organization=organization, start=start, end=end)

    gross_profit = revenue - cogs
    category_totals = expenses(organization=organization, start=start, end=end)
    expenses_total = sum(row.amount for row in category_totals)
    destroyed = write_offs(organization=organization, start=start, end=end)

    return PeriodReport(
        organization_id=str(organization.id),
        tier=tier,
        start=start,
        end=end,
        currency=currency,
        capital_invested=invested,
        revenue=revenue,
        cogs=cogs,
        gross_profit=gross_profit,
        gross_margin_bp=_basis_points(gross_profit, revenue),
        expenses_total=expenses_total,
        expenses=category_totals,
        # Write-offs come off here rather than out of COGS: goods that
        # were destroyed were never sold, so putting them in cost of
        # goods *sold* would misstate the trading margin.
        estimated_operating_result=gross_profit - expenses_total - destroyed,
        estimated_basis=ESTIMATE_BASIS,
        write_offs=destroyed,
        stock_at_risk=stock_at_risk(organization=organization, as_of=end),
        roi_bp=_basis_points(gross_profit, invested),
        cash_revenue=cash,
        insurance_revenue=insurance,
    )


# --------------------------------------------------------------------------
# Series, for the charts
# --------------------------------------------------------------------------


def _months(start: date, end: date) -> list[tuple[date, date, str]]:
    """Whole months spanning the range, clipped to it.

    Clipped rather than rounded outward: a range ending on the 17th must
    not silently report the whole month, which would make the last
    column look like a collapse in trade.
    """
    import calendar

    out = []
    cursor = start.replace(day=1)
    while cursor <= end:
        last = cursor.replace(day=calendar.monthrange(cursor.year, cursor.month)[1])
        out.append(
            (max(cursor, start), min(last, end), cursor.strftime("%b %y"))
        )
        cursor = last + timedelta(days=1)
    return out


def investment_against_revenue(
    *, organization: Organization, start: date, end: date, tier: str = RETAIL
) -> list[dict]:
    """Two RWF series, month by month, for **one axis**.

    Both are money in the same currency. A second axis would invent a
    scale difference that is not there, and a dual axis can be made to
    show any relationship its author wants. The widening or narrowing gap
    between the two lines is the whole message.
    """
    revenue_of = depot_revenue if tier == DEPOT else (
        lambda **kw: retail_revenue(**kw)["total"]
    )
    return [
        {
            "period": label,
            "invested": capital_invested(
                organization=organization, start=month_start, end=month_end
            ),
            "revenue": revenue_of(
                organization=organization, start=month_start, end=month_end
            ),
        }
        for month_start, month_end, label in _months(start, end)
    ]


def inventory_health(
    *, organization: Organization, as_of: date | None = None
) -> list[dict]:
    """Stock value split three ways: stable, slow-moving, expiring.

    The one chart where the status ramp is correct rather than reserved —
    safe, slow-moving and expiring *are* statuses, not arbitrary
    categories.

    Slow-moving is defined by expiry runway rather than by sales
    velocity: a batch with under six months left is the one a clearance
    decision is actually made about, and velocity needs a longer history
    than a new deployment has.
    """

    from django.utils import timezone

    from inventory.models import StockBalance, StockStatus

    as_of = as_of or timezone.localdate()
    balances = StockBalance.objects.filter(
        organization=organization, status=StockStatus.AVAILABLE, quantity_base__gt=0
    ).select_related("batch")

    bands = {"expiring": 0, "slow": 0, "stable": 0}
    for balance in balances:
        value = balance.quantity_base * balance.batch.unit_cost_base
        days = (balance.expiry_date - as_of).days
        if days <= 0:
            continue
        if days <= 90:
            bands["expiring"] += value
        elif days <= 180:
            bands["slow"] += value
        else:
            bands["stable"] += value

    return [
        {
            "band": "Stock value",
            "stable": bands["stable"],
            "slow": bands["slow"],
            "expiring": bands["expiring"],
        }
    ]


def revenue_by_category(
    *,
    organization: Organization,
    start: date,
    end: date,
    tier: str = RETAIL,
    top: int = 3,
) -> list[dict]:
    """Top three therapeutic classes plus Other.

    Thirteen categories against three validated dark slots is not a
    palette problem to solve with more hues — it is a form problem. Bar
    length carries the comparison and colour carries nothing, and the
    tail folds into Other sorted by value. See docs/21 §2.
    """
    if tier == DEPOT:
        from commerce.models import ShipmentLine, ShipmentStatus

        rows = (
            ShipmentLine.objects.filter(
                shipment__organization=organization,
                shipment__status=ShipmentStatus.DISPATCHED,
                shipment__dispatched_at__date__gte=start,
                shipment__dispatched_at__date__lte=end,
            )
            .values("product__category__name")
            .annotate(
                amount=Sum(
                    F("quantity_base")
                    * F("order_line__unit_price")
                    / F("order_line__uom__factor_to_base")
                )
            )
            .order_by("-amount")
        )
    else:
        from sales.models import SaleLine, SaleStatus

        rows = (
            SaleLine.objects.filter(
                sale__organization=organization,
                sale__status=SaleStatus.COMPLETED,
                sale__occurred_at__date__gte=start,
                sale__occurred_at__date__lte=end,
            )
            .values("product__category__name")
            .annotate(amount=Sum(F("line_subtotal") - F("discount")))
            .order_by("-amount")
        )

    ranked = [
        {"category": row["product__category__name"] or "Uncategorised",
         "amount": int(row["amount"] or 0)}
        for row in rows
        if (row["amount"] or 0) > 0
    ]
    head = ranked[:top]
    tail = sum(row["amount"] for row in ranked[top:])
    if tail:
        head.append({"category": "Other", "amount": tail})
    return head


def sales_against_collections(
    *, organization: Organization, start: date, end: date, tier: str = DEPOT
) -> list[dict]:
    """Invoiced against actually banked, month by month.

    The chart a depot extending Net-30 needs. Sales can rise while cash
    falls, and a single revenue line cannot show it — a business can
    trade itself out of working capital while every headline improves.
    """
    from commerce.models import Invoice, InvoiceKind, InvoicePayment, InvoiceStatus

    out = []
    for month_start, month_end, label in _months(start, end):
        invoiced = (
            Invoice.objects.filter(
                organization=organization,
                kind=InvoiceKind.TAX,
                status__in=[
                    InvoiceStatus.ISSUED, InvoiceStatus.PART_PAID, InvoiceStatus.PAID
                ],
                issued_on__gte=month_start,
                issued_on__lte=month_end,
            ).aggregate(total=Sum("total"))["total"]
            or 0
        )
        collected = (
            InvoicePayment.objects.filter(
                organization=organization,
                received_on__gte=month_start,
                received_on__lte=month_end,
            ).aggregate(total=Sum("amount"))["total"]
            or 0
        )
        out.append({"period": label, "invoiced": invoiced, "collected": collected})
    return out


def dashboard(
    *, organization: Organization, start: date, end: date, tier: str = RETAIL
) -> dict:
    """Everything the performance view needs, in one round trip."""
    return {
        "report": period_report(
            organization=organization, start=start, end=end, tier=tier
        ).as_dict(),
        "trend": investment_against_revenue(
            organization=organization, start=start, end=end, tier=tier
        ),
        "inventory_health": inventory_health(organization=organization, as_of=end),
        "revenue_by_category": revenue_by_category(
            organization=organization, start=start, end=end, tier=tier
        ),
        "cash": sales_against_collections(
            organization=organization, start=start, end=end, tier=tier
        ),
    }


# --------------------------------------------------------------------------
# Ageing
# --------------------------------------------------------------------------

#: 0–30, 31–60, 61–90, 90+. The buckets a finance team already thinks in.
BUCKETS = [(0, 30), (31, 60), (61, 90), (91, None)]


def receivables_ageing(*, supplier: Organization, as_of: date | None = None) -> dict:
    """Who owes the depot, and for how long.

    Per customer as well as per bucket: the bucket says how bad the
    problem is, the customer says who to ring about it.
    """
    from django.utils import timezone

    from commerce.models import Invoice, InvoiceKind, InvoiceStatus

    as_of = as_of or timezone.localdate()
    buckets = {_label(low, high): 0 for low, high in BUCKETS}
    by_customer: dict = {}
    total = 0

    invoices = Invoice.objects.filter(
        organization=supplier,
        kind=InvoiceKind.TAX,
        status__in=[InvoiceStatus.ISSUED, InvoiceStatus.PART_PAID],
    ).select_related("customer")

    for invoice in invoices:
        outstanding = invoice.outstanding
        if outstanding <= 0:
            continue
        days = invoice.days_overdue(as_of=as_of)
        label = _bucket_for(days)
        buckets[label] += outstanding
        total += outstanding

        entry = by_customer.setdefault(
            str(invoice.customer_id),
            {
                "customer": invoice.customer.name,
                "total": 0,
                **{_label(low, high): 0 for low, high in BUCKETS},
            },
        )
        entry[label] += outstanding
        entry["total"] += outstanding

    return {
        "as_of": as_of.isoformat(),
        "buckets": buckets,
        "total": total,
        "customers": sorted(by_customer.values(), key=lambda row: -row["total"]),
    }


def _label(low: int, high: int | None) -> str:
    return f"{low}-{high}" if high is not None else f"{low}+"


def _bucket_for(days: int) -> str:
    for low, high in BUCKETS:
        if high is None or days <= high:
            return _label(low, high)
    return _label(*BUCKETS[-1])
