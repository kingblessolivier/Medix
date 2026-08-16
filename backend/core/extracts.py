"""Regulator extracts.

What an inspector asks for, assembled from records that already exist
rather than from a report table maintained alongside them. A summary that
can drift from the register it summarises is worse than no summary.

Every extract is **read-only and dated**. Nothing here writes, and every
figure is bounded by the range asked for, so two inspectors asking about
two periods get two answers rather than one moving one.

See docs/06-compliance.md and docs/29-alerts.md §4.
"""

from __future__ import annotations

from datetime import date

from django.db.models import Sum

from core import quotas
from core.models import Organization


def controlled_register(
    *, organization: Organization, start: date, end: date, schedule: str = ""
) -> dict:
    """The statutory register for a period, as dispensed.

    Reads `sales.ControlledDeliveryEntry`, which is append-only and
    carries the patient name and address the law requires. It is not
    reconstructed from sales — a register rebuilt from another table is a
    register that can disagree with itself.
    """
    from sales.models import ControlledDeliveryEntry

    entries = ControlledDeliveryEntry.objects.filter(
        organization=organization,
        entered_at__date__gte=start,
        entered_at__date__lte=end,
    ).order_by("entered_at")
    if schedule:
        entries = entries.filter(schedule=schedule)

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "entries": [
            {
                "entered_at": entry.entered_at.isoformat(),
                "substance": entry.substance_denomination,
                "schedule": entry.schedule,
                "quantity_base": entry.quantity_base,
                "uom_code": entry.uom_code,
                "patient_name": entry.patient_name,
                "patient_address": entry.patient_address,
                "dispensed_by": str(entry.dispensed_by),
                "council_number": entry.dispensed_by_council_number,
                "balance_after_base": entry.balance_after_base,
            }
            for entry in entries
        ],
        "count": entries.count(),
    }


def controlled_movements(
    *, organization: Organization, start: date, end: date, schedule: str = ""
) -> dict:
    """Everything that moved, in and out, per schedule.

    The register covers dispensing to patients. This covers the rest —
    receipts, wholesale dispatch, write-offs — which is what makes the
    two reconcilable: opening plus in, less out, should equal closing.
    """
    from inventory.models import StockMovement

    movements = StockMovement.objects.filter(
        organization=organization,
        batch__product__legal_status="CONTROLLED",
        occurred_at__date__gte=start,
        occurred_at__date__lte=end,
    ).select_related("batch__product")
    if schedule:
        movements = movements.filter(batch__product__controlled_schedule=schedule)

    rows = (
        movements.values(
            "batch__product__controlled_schedule",
            "batch__product__name",
            "kind",
        )
        .annotate(quantity_base=Sum("quantity_base"))
        .order_by("batch__product__controlled_schedule", "batch__product__name")
    )

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "movements": [
            {
                "schedule": row["batch__product__controlled_schedule"],
                "product": row["batch__product__name"],
                "kind": row["kind"],
                "quantity_base": row["quantity_base"],
            }
            for row in rows
        ],
    }


def controlled_transfers(
    *, organization: Organization, start: date, end: date
) -> dict:
    """Chain-of-custody forms raised in the period, and their state.

    An unsigned form is the finding: the goods left, and only one
    pharmacist is on record for them.
    """
    from commerce.models import ControlledTransfer

    transfers = ControlledTransfer.objects.filter(
        organization=organization,
        released_at__date__gte=start,
        released_at__date__lte=end,
    ).select_related("shipment__order__organization", "released_by__user")

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "transfers": [
            {
                "number": transfer.number,
                "delivery_note": transfer.shipment.number,
                "recipient": transfer.shipment.order.organization.name,
                "released_by": str(transfer.released_by.user),
                "released_registration": transfer.released_by.council_number,
                "released_at": (
                    transfer.released_at.isoformat() if transfer.released_at else None
                ),
                "received_by": transfer.received_by_name,
                "received_at": (
                    transfer.received_at.isoformat() if transfer.received_at else None
                ),
                "complete": transfer.is_complete,
            }
            for transfer in transfers
        ],
        "incomplete": sum(1 for t in transfers if not t.is_complete),
    }


def quota_usage(
    *, organization: Organization, as_of: date | None = None
) -> list[dict]:
    """Each schedule against its cap, for the period in force."""
    from core.quotas import ControlledQuota

    seen = set()
    out = []
    for quota in ControlledQuota.objects.filter(organization=organization):
        if quota.schedule in seen:
            continue
        seen.add(quota.schedule)
        standing = quotas.position(
            organization=organization, schedule=quota.schedule, as_of=as_of
        )
        out.append(
            {
                "schedule": standing.schedule,
                "limit_base": standing.limit_base,
                "used_base": standing.used_base,
                "remaining_base": standing.remaining,
                "period_start": standing.period_start.isoformat(),
                "period_end": standing.period_end.isoformat(),
            }
        )
    return out


def bundle(*, organization: Organization, start: date, end: date) -> dict:
    """Everything an inspection asks for, in one extract."""
    return {
        "organization": organization.name,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "register": controlled_register(
            organization=organization, start=start, end=end
        ),
        "movements": controlled_movements(
            organization=organization, start=start, end=end
        ),
        "transfers": controlled_transfers(
            organization=organization, start=start, end=end
        ),
        "quotas": quota_usage(organization=organization, as_of=end),
    }
