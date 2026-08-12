"""Operational alerts: expiry, reorder point, storage class.

Every threshold is read through `core.alerts.rule_for`, so a pharmacy
that tightens its short-dated window to 120 days does not retroactively
make last quarter's decisions look negligent.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.db.models import Sum
from django.utils import timezone

from core.alerts import Alert, Severity, about, rule_for
from core.models import Organization
from inventory.models import Batch, StockBalance, StockStatus


def short_dated_batches(
    *, organization: Organization, as_of: date | None = None
) -> list[Alert]:
    """Batches close enough to expiry to need rotating or clearing.

    Only batches with stock still on them. A short-dated batch that has
    already gone is not a warning, it is history, and warning about it is
    exactly the noise that teaches people to click through.
    """
    as_of = as_of or timezone.localdate()
    rule = rule_for(organization=organization, code="SHORT_DATED_BATCH", as_of=as_of)
    horizon = as_of + timedelta(days=rule["threshold"].get("days", 90))

    holdings = (
        StockBalance.objects.filter(
            organization=organization,
            batch__expiry_date__lte=horizon,
            batch__expiry_date__gt=as_of,
            status=StockStatus.AVAILABLE,
        )
        .values("batch")
        .annotate(quantity=Sum("quantity_base"))
        .filter(quantity__gt=0)
    )
    batches = {
        batch.id: batch
        for batch in Batch.objects.filter(
            id__in=[row["batch"] for row in holdings]
        ).select_related("product")
    }

    alerts = []
    for row in holdings:
        batch = batches[row["batch"]]
        days = (batch.expiry_date - as_of).days
        alerts.append(
            about(
                batch,
                code="SHORT_DATED_BATCH",
                severity=rule["severity"],
                title=f"{batch.product.name} expires in {days} days",
                detail=f"Batch {batch.batch_number} · {batch.expiry_date:%d %b %Y}",
                meta={"days": days, "quantity_base": row["quantity"]},
            )
        )
    return sorted(alerts, key=lambda alert: alert.meta["days"])


def below_reorder_point(*, organization: Organization) -> list[Alert]:
    """Products the pharmacy is about to run out of.

    `Product.reorder_point_base` has existed since the catalogue landed
    and nothing read it. Zero means unset, not "reorder at nothing".
    """
    from catalog.models import Product

    rule = rule_for(organization=organization, code="BELOW_REORDER_POINT")

    holdings = dict(
        StockBalance.objects.filter(
            organization=organization, status=StockStatus.AVAILABLE
        )
        .values_list("product")
        .annotate(quantity=Sum("quantity_base"))
    )

    alerts = []
    for product in Product.objects.filter(
        organization=organization, is_active=True, reorder_point_base__gt=0
    ):
        on_hand = holdings.get(product.id, 0)
        if on_hand >= product.reorder_point_base:
            continue
        alerts.append(
            about(
                product,
                code="BELOW_REORDER_POINT",
                severity=rule["severity"],
                title=f"{product.name} below reorder point",
                detail=f"{on_hand:,} on hand · reorder at {product.reorder_point_base:,}",
                meta={"on_hand": on_hand, "reorder_point": product.reorder_point_base},
            )
        )
    return alerts


def storage_class_mismatch(*, product, location) -> list[Alert]:
    """A cold-chain product going into an ambient location.

    Critical: the goods are damaged by the time anyone reads a warning,
    so this refuses rather than asks.
    """
    from inventory.models import TemperatureClass

    if not product.cold_chain:
        return []
    if location.temperature_class in (TemperatureClass.CHILLED, TemperatureClass.FROZEN):
        return []
    return [
        about(
            location,
            code="STORAGE_CLASS_MISMATCH",
            severity=Severity.CRITICAL,
            title=f"{location.name} is not cold storage",
            detail=f"{product.name} requires 2–8°C.",
            meta={"temperature_class": location.temperature_class},
        )
    ]
