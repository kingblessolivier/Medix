"""Stock movements that had a `MovementKind` and no service.

Six kinds were declared when the ledger was built and never given a way
to happen: transfer out and in, sale return, supplier return, recall and
manual quarantine. An enum value nothing can produce is a promise the
schema makes and the code does not keep.

Every one of these goes through `post_movement`. None of them touches a
balance directly — that rule is the reason a recall can be traced at all.

See docs/05-modules.md §2 and docs/06-compliance.md.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Sum

from core import audit, sequences
from core.exceptions import DomainError
from core.models import Organization, User
from core.quantity import Quantity, from_base
from inventory import services
from inventory.models import (
    Batch,
    Location,
    MovementKind,
    StockBalance,
    StockMovement,
    StockStatus,
)


class SameLocation(DomainError):
    default_code = "same_location"
    default_detail = "A transfer needs two different locations."


# --------------------------------------------------------------------------
# Transfer
# --------------------------------------------------------------------------


@transaction.atomic
def transfer(
    *,
    organization: Organization,
    batch: Batch,
    from_location: Location,
    to_location: Location,
    quantity: Quantity,
    performed_by: User,
    reason: str = "",
    idempotency_key: str | None = None,
) -> dict:
    """Move stock between two locations in one organization.

    **Two movements, not one.** Stock leaves one location and arrives at
    another, and a single row with a location change would leave neither
    balance reconstructible from the ledger. The pair shares a reference
    so they can be read as one event.

    Cold chain is enforced on arrival by `post_movement`, so a refrigerated
    batch cannot be transferred into an ambient store — the check is not
    repeated here, deliberately: one enforcement point, not two that can
    drift.
    """
    if from_location.id == to_location.id:
        raise SameLocation()
    if from_location.organization_id != organization.id or (
        to_location.organization_id != organization.id
    ):
        raise DomainError(
            "Both locations must belong to this organization.",
            code="cross_organization_transfer",
        )
    if quantity.base_value <= 0:
        raise DomainError("Transfer a positive quantity.", code="non_positive_transfer")

    reference = sequences.next_number(organization, "TRANSFER")

    out = services.post_movement(
        organization=organization,
        location=from_location,
        batch=batch,
        kind=MovementKind.TRANSFER_OUT,
        quantity=-quantity,
        performed_by=performed_by,
        reference=reference,
        reason=reason,
        idempotency_key=f"{idempotency_key}:out" if idempotency_key else None,
    )
    into = services.post_movement(
        organization=organization,
        location=to_location,
        batch=batch,
        kind=MovementKind.TRANSFER_IN,
        quantity=quantity,
        performed_by=performed_by,
        reference=reference,
        reason=reason,
        idempotency_key=f"{idempotency_key}:in" if idempotency_key else None,
    )

    audit.record(
        action="inventory.stock.transferred",
        subject=batch,
        actor=performed_by,
        after={
            "reference": reference,
            "batch": batch.batch_number,
            "quantity_base": quantity.base_value,
            "from": from_location.name,
            "to": to_location.name,
            "reason": reason,
        },
        organization=organization,
    )
    return {"reference": reference, "out": out, "in": into}


# --------------------------------------------------------------------------
# Returns
# --------------------------------------------------------------------------


@transaction.atomic
def sale_return(
    *,
    organization: Organization,
    sale_line,
    quantity: Quantity,
    performed_by: User,
    reason: str,
    restock: bool = True,
) -> StockMovement | None:
    """A customer brings goods back.

    `restock` is the decision that matters and it is the caller's, not a
    default: medicine that has left the premises may not be resaleable,
    and putting it back on the shelf because the software assumed so is
    how an unsafe pack re-enters the supply chain. Refusing to restock
    still records the return — the sale is reversed either way.

    Returned to the **same batch** it was dispensed from, which is why
    `SaleLine` holds the batch.
    """
    if not reason.strip():
        raise DomainError("A return needs a reason.", code="reason_required")
    if quantity.base_value <= 0:
        raise DomainError("Return a positive quantity.", code="non_positive_return")
    if quantity.base_value > sale_line.quantity_base:
        raise DomainError(
            "That is more than was dispensed.",
            code="over_return",
            meta={"dispensed_base": sale_line.quantity_base},
        )

    movement = None
    if restock:
        movement = services.post_movement(
            organization=organization,
            location=sale_line.sale.location,
            batch=sale_line.batch,
            kind=MovementKind.SALE_RETURN,
            quantity=quantity,
            performed_by=performed_by,
            reference=sale_line.sale.number or "",
            reason=reason.strip(),
        )

    audit.record(
        action="sales.line.returned",
        subject=sale_line.sale,
        actor=performed_by,
        after={
            "product": sale_line.product.name,
            "batch": sale_line.batch.batch_number,
            "quantity_base": quantity.base_value,
            "restocked": restock,
            "reason": reason.strip(),
        },
        organization=organization,
    )
    return movement


@transaction.atomic
def supplier_return(
    *,
    organization: Organization,
    batch: Batch,
    location: Location,
    quantity: Quantity,
    performed_by: User,
    reason: str,
    status: str = StockStatus.AVAILABLE,
) -> StockMovement:
    """Goods going back to the supplier.

    Takes stock from whichever status it sits in, because the common case
    is returning something quarantined — a short-dated delivery, a batch
    that arrived warm — and forcing it through release first would put
    unusable stock into available on the way out.
    """
    if not reason.strip():
        raise DomainError("A supplier return needs a reason.", code="reason_required")

    movement = services.post_movement(
        organization=organization,
        location=location,
        batch=batch,
        kind=MovementKind.SUPPLIER_RETURN,
        quantity=-quantity,
        performed_by=performed_by,
        status=status,
        reason=reason.strip(),
    )
    audit.record(
        action="inventory.batch.returned_to_supplier",
        subject=batch,
        actor=performed_by,
        after={
            "batch": batch.batch_number,
            "quantity_base": quantity.base_value,
            "location": location.name,
            "reason": reason.strip(),
        },
        organization=organization,
    )
    return movement


# --------------------------------------------------------------------------
# Quarantine and recall
# --------------------------------------------------------------------------


@transaction.atomic
def quarantine(
    *,
    organization: Organization,
    batch: Batch,
    location: Location,
    quantity: Quantity,
    performed_by: User,
    reason: str,
) -> list[StockMovement]:
    """Hold stock without removing it.

    A status move, not a stock movement: the goods are still on the
    premises and still owned. Two rows, one out of available and one into
    quarantined, so both balances stay derivable from the ledger.
    """
    if not reason.strip():
        raise DomainError("Quarantine needs a reason.", code="reason_required")

    return [
        services.post_movement(
            organization=organization,
            location=location,
            batch=batch,
            kind=MovementKind.QUARANTINE,
            quantity=-quantity,
            performed_by=performed_by,
            status=StockStatus.AVAILABLE,
            reason=reason.strip(),
        ),
        services.post_movement(
            organization=organization,
            location=location,
            batch=batch,
            kind=MovementKind.QUARANTINE,
            quantity=quantity,
            performed_by=performed_by,
            status=StockStatus.QUARANTINED,
            reason=reason.strip(),
        ),
    ]


@transaction.atomic
def recall(
    *,
    organization: Organization,
    batch: Batch,
    performed_by: User,
    reason: str,
    authority_reference: str = "",
) -> dict:
    """Pull a batch out of circulation everywhere it is held.

    **Every location at once.** A recall that has to be run store by
    store is a recall that misses one, and the whole reason the ledger
    records location per movement is so this query is possible.

    Reports the trace as well as moving the stock: who this batch was
    sold to, and which pharmacies it was dispatched to, so the people
    holding the rest of it can be told. That list is the point of a
    recall — the stock still on our own shelves is the easy part.
    """
    if not reason.strip():
        raise DomainError("A recall needs a reason.", code="reason_required")

    reference = sequences.next_number(organization, "RECALL")
    held = StockBalance.objects.filter(
        organization=organization, batch=batch, quantity_base__gt=0
    ).exclude(status=StockStatus.RECALLED).select_related("location")

    moved = []
    for balance in held:
        quantity = from_base(balance.quantity_base, batch.product.base_uom)
        # Out of whatever status it sat in, into recalled. Available and
        # quarantined stock are both recalled; nothing is left behind
        # because it happened to be on hold already.
        services.post_movement(
            organization=organization,
            location=balance.location,
            batch=batch,
            kind=MovementKind.RECALL,
            quantity=-quantity,
            performed_by=performed_by,
            status=balance.status,
            reference=reference,
            reason=reason.strip(),
        )
        moved.append(
            services.post_movement(
                organization=organization,
                location=balance.location,
                batch=batch,
                kind=MovementKind.RECALL,
                quantity=quantity,
                performed_by=performed_by,
                status=StockStatus.RECALLED,
                reference=reference,
                reason=reason.strip(),
            )
        )

    trace = trace_batch(organization=organization, batch=batch)

    audit.record(
        action="inventory.batch.recalled",
        subject=batch,
        actor=performed_by,
        after={
            "reference": reference,
            "batch": batch.batch_number,
            "product": batch.product.name,
            "reason": reason.strip(),
            "authority_reference": authority_reference,
            "locations": len(moved),
            "quantity_base": sum(abs(m.movement.quantity_base) for m in moved),
            "patients": len(trace["patients"]),
            "customers": len(trace["customers"]),
        },
        organization=organization,
    )
    return {
        "reference": reference,
        "quantity_base": sum(abs(m.movement.quantity_base) for m in moved),
        "locations": len(moved),
        "trace": trace,
    }


def trace_batch(*, organization: Organization, batch: Batch) -> dict:
    """Everywhere this batch went. The question a recall actually asks.

    Two directions: patients it was dispensed to, and pharmacies it was
    dispatched to. Both are read from records that already exist — the
    sale line holds the batch and so does the shipment line, which is why
    neither had to be designed for this.
    """
    from commerce.models import ShipmentLine
    from sales.models import SaleLine

    dispensed = (
        SaleLine.objects.filter(sale__organization=organization, batch=batch)
        .select_related("sale__patient", "sale")
        .order_by("sale__occurred_at")
    )
    patients = [
        {
            "sale": line.sale.number,
            "occurred_at": line.sale.occurred_at.isoformat(),
            "patient": line.sale.patient.full_name if line.sale.patient_id else "",
            "phone": line.sale.patient.phone if line.sale.patient_id else "",
            "quantity_base": line.quantity_base,
        }
        for line in dispensed
    ]

    shipped = (
        ShipmentLine.objects.filter(shipment__organization=organization, batch=batch)
        .select_related("shipment__order__organization", "shipment")
        .order_by("shipment__dispatched_at")
    )
    customers = [
        {
            "delivery_note": line.shipment.number,
            "dispatched_at": (
                line.shipment.dispatched_at.isoformat()
                if line.shipment.dispatched_at
                else None
            ),
            "customer": line.shipment.order.organization.name,
            "quantity_base": line.quantity_base,
        }
        for line in shipped
    ]

    return {
        "batch": batch.batch_number,
        "product": batch.product.name,
        "expiry_date": batch.expiry_date.isoformat(),
        "patients": patients,
        "customers": customers,
        "dispensed_base": sum(row["quantity_base"] for row in patients),
        "dispatched_base": sum(row["quantity_base"] for row in customers),
        "on_hand_base": (
            StockBalance.objects.filter(
                organization=organization, batch=batch
            ).aggregate(total=Sum("quantity_base"))["total"]
            or 0
        ),
    }
