"""Controlled substance quotas.

A pharmacy may hold and move only so much of a scheduled substance in a
period. The limit is set by the regulator, per schedule, and it changes —
so it is effective-dated configuration on the same footing as a tax rule,
never a constant.

The check is deliberately **on movement, not on holding**. What the
regulator caps is throughput: how much of a narcotic passed through this
premises this month. A snapshot of what is on the shelf answers a
different question and would let a pharmacy cycle far more than its quota
while never appearing to hold much.

See docs/29-alerts.md §4.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from django.db import models
from django.db.models import Sum
from django.utils import timezone

from core.alerts import Alert, Severity, about, rule_for
from core.models import ControlledQuota, Organization, QuotaPeriod


@dataclass(frozen=True)
class QuotaPosition:
    schedule: str
    limit_base: int
    used_base: int
    pending_base: int
    period_start: date
    period_end: date

    @property
    def exposure(self) -> int:
        return self.used_base + self.pending_base

    @property
    def remaining(self) -> int:
        return max(0, self.limit_base - self.exposure)

    @property
    def applies(self) -> bool:
        return self.limit_base > 0


def period_bounds(period: str, as_of: date) -> tuple[date, date]:
    """Calendar boundaries. A quota resets on the calendar, not on signup."""
    if period == QuotaPeriod.YEAR:
        return date(as_of.year, 1, 1), date(as_of.year, 12, 31)
    if period == QuotaPeriod.QUARTER:
        first_month = 3 * ((as_of.month - 1) // 3) + 1
        last_month = first_month + 2
        return (
            date(as_of.year, first_month, 1),
            date(
                as_of.year,
                last_month,
                calendar.monthrange(as_of.year, last_month)[1],
            ),
        )
    return (
        as_of.replace(day=1),
        as_of.replace(day=calendar.monthrange(as_of.year, as_of.month)[1]),
    )


def quota_for(
    *, organization: Organization, schedule: str, as_of: date | None = None
) -> ControlledQuota | None:
    """The quota in force on `as_of`.

    Dated, so a movement from six months ago stays explainable under the
    limit that applied then rather than under today's.
    """
    as_of = as_of or timezone.localdate()
    return (
        ControlledQuota.objects.filter(
            organization=organization, schedule=schedule, effective_from__lte=as_of
        )
        .filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=as_of))
        .order_by("-effective_from")
        .first()
    )


def position(
    *,
    organization: Organization,
    schedule: str,
    pending_base: int = 0,
    as_of: date | None = None,
) -> QuotaPosition:
    """What has moved this period, including what is about to.

    `pending_base` is the consignment under consideration. Counting only
    history lets a pharmacy at its limit move one more consignment every
    time, because the new one is never part of the sum — the same trap
    the credit check exists to close.
    """
    as_of = as_of or timezone.localdate()
    quota = quota_for(organization=organization, schedule=schedule, as_of=as_of)
    if quota is None:
        return QuotaPosition(schedule, 0, 0, pending_base, as_of, as_of)

    start, end = period_bounds(quota.period, as_of)

    from inventory.models import MovementKind, StockMovement

    # Outward movements only, and by absolute value: what the regulator
    # caps is how much left, and an inward receipt is not throughput.
    used = (
        StockMovement.objects.filter(
            organization=organization,
            batch__product__controlled_schedule=schedule,
            kind__in=[MovementKind.SALE, MovementKind.WHOLESALE_DISPATCH],
            occurred_at__date__gte=start,
            occurred_at__date__lte=end,
        ).aggregate(total=Sum("quantity_base"))["total"]
        or 0
    )

    return QuotaPosition(
        schedule=schedule,
        limit_base=quota.limit_base,
        used_base=abs(used),
        pending_base=pending_base,
        period_start=start,
        period_end=end,
    )


def check(
    *,
    organization: Organization,
    schedule: str,
    pending_base: int = 0,
    as_of: date | None = None,
) -> list[Alert]:
    """Blocked over the quota, warned approaching it."""
    standing = position(
        organization=organization,
        schedule=schedule,
        pending_base=pending_base,
        as_of=as_of,
    )
    if not standing.applies:
        return []

    if standing.exposure > standing.limit_base:
        return [
            about(
                None,
                code="CONTROLLED_QUOTA_EXCEEDED",
                severity=Severity.CRITICAL,
                title=f"Schedule {schedule} quota exceeded",
                detail=(
                    f"{standing.exposure:,} against {standing.limit_base:,} "
                    f"for {standing.period_start:%b %Y}."
                ),
                meta={
                    "schedule": schedule,
                    "limit_base": standing.limit_base,
                    "exposure": standing.exposure,
                },
            )
        ]

    rule = rule_for(
        organization=organization, code="CONTROLLED_QUOTA_NEAR", as_of=as_of
    )
    percent = rule["threshold"].get("percent", 80)
    if standing.exposure * 100 >= standing.limit_base * percent:
        return [
            about(
                None,
                code="CONTROLLED_QUOTA_NEAR",
                severity=rule["severity"],
                title=f"Schedule {schedule} quota nearly used",
                detail=(
                    f"{standing.exposure * 100 // standing.limit_base}% of "
                    f"{standing.limit_base:,} for {standing.period_start:%b %Y}."
                ),
                meta={
                    "schedule": schedule,
                    "limit_base": standing.limit_base,
                    "exposure": standing.exposure,
                },
            )
        ]
    return []
