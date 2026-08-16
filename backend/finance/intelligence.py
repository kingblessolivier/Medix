"""What actually makes money, and what is quietly costing it.

Phase 7. Every figure here is computed from records that already exist —
batch cost on the line, movements in the ledger — rather than from a
summary table, for the same reason the period report is: a stored total
is only true until something is backdated.

**Margin is exact, not estimated.** `SaleLine` holds the batch it was
allocated from and the cost of that batch, so the margin on a line is the
real margin on the real goods rather than revenue against a moving
average.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.db.models import Count, F, Sum
from django.utils import timezone

from core.models import Organization


def _basis_points(part: int, whole: int) -> int | None:
    """None when there is nothing to divide by — not zero.

    A product with no revenue and a product sold exactly at cost are
    different facts, and returning 0 for both makes the first look like
    the second.
    """
    return None if whole == 0 else part * 10_000 // whole


@dataclass(frozen=True)
class MarginRow:
    key: str
    label: str
    revenue: int
    cogs: int

    @property
    def gross_profit(self) -> int:
        return self.revenue - self.cogs

    @property
    def margin_bp(self) -> int | None:
        return _basis_points(self.gross_profit, self.revenue)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "revenue": self.revenue,
            "cogs": self.cogs,
            "gross_profit": self.gross_profit,
            "margin_bp": self.margin_bp,
        }


def _margin_by(
    *, organization: Organization, start: date, end: date, group: str, label: str
) -> list[MarginRow]:
    from sales.models import SaleLine, SaleStatus

    rows = (
        SaleLine.objects.filter(
            sale__organization=organization,
            sale__status=SaleStatus.COMPLETED,
            sale__occurred_at__date__gte=start,
            sale__occurred_at__date__lte=end,
        )
        .values(group, label)
        .annotate(
            revenue=Sum(F("line_subtotal") - F("discount")),
            cogs=Sum(F("quantity_base") * F("unit_cost_base")),
        )
        .order_by("-revenue")
    )
    return [
        MarginRow(
            key=str(row[group] or ""),
            label=row[label] or "Uncategorised",
            revenue=int(row["revenue"] or 0),
            cogs=int(row["cogs"] or 0),
        )
        for row in rows
    ]


def margin_by_category(
    *, organization: Organization, start: date, end: date
) -> list[MarginRow]:
    """What actually makes money, by therapeutic class."""
    return _margin_by(
        organization=organization,
        start=start,
        end=end,
        group="product__category_id",
        label="product__category__name",
    )


def margin_by_product(
    *, organization: Organization, start: date, end: date, limit: int = 20
) -> list[MarginRow]:
    return _margin_by(
        organization=organization,
        start=start,
        end=end,
        group="product_id",
        label="product__name",
    )[:limit]


def best_sellers(
    *, organization: Organization, start: date, end: date, limit: int = 10
) -> list[dict]:
    """By units, not by revenue.

    Two different questions, and this is the stock one: what is leaving
    the shelf. Ranking by revenue would put one expensive item above a
    fast mover and mislead the reorder decision this feeds.
    """
    from sales.models import SaleLine, SaleStatus

    rows = (
        SaleLine.objects.filter(
            sale__organization=organization,
            sale__status=SaleStatus.COMPLETED,
            sale__occurred_at__date__gte=start,
            sale__occurred_at__date__lte=end,
        )
        .values("product_id", "product__name")
        .annotate(
            units=Sum("quantity_base"),
            revenue=Sum(F("line_subtotal") - F("discount")),
            sales=Count("sale_id", distinct=True),
        )
        .order_by("-units")[:limit]
    )
    return [
        {
            "product": str(row["product_id"]),
            "name": row["product__name"],
            "units": int(row["units"] or 0),
            "revenue": int(row["revenue"] or 0),
            "sales": row["sales"],
        }
        for row in rows
    ]


def slow_movers(
    *, organization: Organization, start: date, end: date, limit: int = 20
) -> list[dict]:
    """Stock held that barely moved, with the capital it is tying up.

    Held-but-unsold is the expensive half of inventory and the half no
    screen usually shows: it does not appear in a sales report by
    definition, and an expiry report only catches it once it is nearly
    too late.
    """
    from inventory.models import StockBalance, StockStatus
    from sales.models import SaleLine, SaleStatus

    sold = dict(
        SaleLine.objects.filter(
            sale__organization=organization,
            sale__status=SaleStatus.COMPLETED,
            sale__occurred_at__date__gte=start,
            sale__occurred_at__date__lte=end,
        )
        .values_list("product_id")
        .annotate(units=Sum("quantity_base"))
    )

    held = (
        StockBalance.objects.filter(
            organization=organization,
            status=StockStatus.AVAILABLE,
            quantity_base__gt=0,
        )
        .values("product_id", "product__name")
        .annotate(
            units=Sum("quantity_base"),
            value=Sum(F("quantity_base") * F("batch__unit_cost_base")),
        )
    )

    rows = []
    for row in held:
        moved = int(sold.get(row["product_id"], 0))
        on_hand = int(row["units"] or 0)
        # Months of cover at the rate it actually sold. None means it did
        # not sell at all, which is the worst case and sorts first.
        days = (end - start).days or 1
        daily = moved / days
        cover_days = None if daily == 0 else int(on_hand / daily)
        rows.append(
            {
                "product": str(row["product_id"]),
                "name": row["product__name"],
                "on_hand": on_hand,
                "sold": moved,
                "value": int(row["value"] or 0),
                "cover_days": cover_days,
            }
        )

    # Never sold first, then longest cover. Both are capital sitting
    # still; the first is capital that may never come back.
    rows.sort(key=lambda row: (row["cover_days"] is not None, -(row["cover_days"] or 0)))
    return rows[:limit]


def stock_outs(
    *, organization: Organization, start: date, end: date, limit: int = 20
) -> list[dict]:
    """Products that sold in the period and are now at zero.

    A stock-out on a fast mover is lost revenue that leaves no trace in
    any sales figure — the sale simply did not happen — so it has to be
    inferred from the gap between demand and holding.
    """
    from inventory.models import StockBalance, StockStatus
    from sales.models import SaleLine, SaleStatus

    sold = (
        SaleLine.objects.filter(
            sale__organization=organization,
            sale__status=SaleStatus.COMPLETED,
            sale__occurred_at__date__gte=start,
            sale__occurred_at__date__lte=end,
        )
        .values("product_id", "product__name")
        .annotate(units=Sum("quantity_base"))
        .order_by("-units")
    )

    held = dict(
        StockBalance.objects.filter(
            organization=organization, status=StockStatus.AVAILABLE
        )
        .values_list("product_id")
        .annotate(units=Sum("quantity_base"))
    )

    return [
        {
            "product": str(row["product_id"]),
            "name": row["product__name"],
            "sold": int(row["units"] or 0),
            "on_hand": int(held.get(row["product_id"], 0)),
        }
        for row in sold
        if int(held.get(row["product_id"], 0)) == 0
    ][:limit]


def report(
    *, organization: Organization, start: date | None = None, end: date | None = None
) -> dict:
    """Everything Phase 7 answers, in one round trip."""
    end = end or timezone.localdate()
    start = start or (end - timedelta(days=90))

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "by_category": [row.as_dict() for row in margin_by_category(
            organization=organization, start=start, end=end
        )],
        "by_product": [row.as_dict() for row in margin_by_product(
            organization=organization, start=start, end=end
        )],
        "best_sellers": best_sellers(organization=organization, start=start, end=end),
        "slow_movers": slow_movers(organization=organization, start=start, end=end),
        "stock_outs": stock_outs(organization=organization, start=start, end=end),
    }
