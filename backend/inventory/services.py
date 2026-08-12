"""Inventory rules. The only sanctioned way stock changes.

Two functions carry the system:

    post_movement()  — the single write path into the ledger
    allocate_fefo()  — nearest expiry first, always

Nothing else may write StockMovement or StockBalance.

See docs/03-data-model.md, ADR-001, ADR-002.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from catalog.models import Product
from core.exceptions import DomainError, InsufficientStock
from core.models import Organization, User
from core.quantity import Quantity
from inventory.models import (
    Allocation,
    Batch,
    Location,
    MovementKind,
    StockBalance,
    StockMovement,
    StockStatus,
)

# Movements that reduce stock. Used to validate sign against kind.
OUTBOUND = {
    MovementKind.SALE,
    MovementKind.WHOLESALE_DISPATCH,
    MovementKind.TRANSFER_OUT,
    MovementKind.DISPOSAL,
    MovementKind.EXPIRY_WRITE_OFF,
    MovementKind.SUPPLIER_RETURN,
}

INBOUND = {
    MovementKind.OPENING,
    MovementKind.PURCHASE_RECEIPT,
    MovementKind.SALE_RETURN,
    MovementKind.TRANSFER_IN,
}

# Kinds where direction is genuinely the caller's to state: a stock-take
# adjustment goes either way, and the status transitions move a quantity
# between statuses rather than in or out.
CALLER_SIGNED = {
    MovementKind.ADJUSTMENT,
    MovementKind.QUARANTINE,
    MovementKind.RELEASE,
    MovementKind.RECALL,
}


class ColdChainViolation(DomainError):
    default_code = "cold_chain_violation"
    default_detail = "This batch requires refrigerated storage."


class ExpiredBatch(DomainError):
    default_code = "batch_expired"
    default_detail = "This batch has expired."


@dataclass(frozen=True)
class MovementResult:
    movement: StockMovement
    balance_after_base: int
    replayed: bool = False


@transaction.atomic
def post_movement(
    *,
    organization: Organization,
    location: Location,
    batch: Batch,
    kind: str,
    quantity: Quantity,
    performed_by: User | None = None,
    status: str = StockStatus.AVAILABLE,
    reason: str = "",
    reference: str = "",
    occurred_at: datetime | None = None,
    idempotency_key: str | None = None,
) -> MovementResult:
    """Append one movement and update the projection in the same transaction.

    Preserves: the ledger is the only source of truth for stock, and the
    projection never diverges from it under normal operation.

    Raises InsufficientStock rather than allowing a negative balance.
    """
    occurred_at = occurred_at or timezone.now()
    idempotency_key = idempotency_key or str(uuid.uuid4())

    # An agent may retry indefinitely after losing its connection; the same
    # key must not apply twice.
    existing = StockMovement.objects.filter(
        organization=organization, idempotency_key=idempotency_key
    ).first()
    if existing is not None:
        return MovementResult(existing, existing.balance_after_base, replayed=True)

    base = quantity.base_value
    if base == 0:
        raise DomainError("Quantity cannot be zero.", code="zero_quantity")

    # Sign is derived from the kind, not trusted from the caller.
    #
    # An unclassified kind is refused rather than defaulted. Falling
    # through used to keep the caller's positive sign, so adding a kind
    # and forgetting to classify it silently *created* stock — which is
    # exactly what WHOLESALE_DISPATCH did on its first run.
    if kind in OUTBOUND:
        base = -abs(base)
    elif kind in INBOUND:
        base = abs(base)
    elif kind not in CALLER_SIGNED:
        raise DomainError(
            f"Movement kind {kind} is not classified as inbound, outbound "
            "or caller-signed. Classify it in inventory.services.",
            code="unclassified_movement_kind",
        )

    if base > 0:
        _guard_cold_chain(batch, location)

    balance = _lock_balance(
        organization=organization, location=location, batch=batch, status=status
    )
    new_quantity = balance.quantity_base + base

    if new_quantity < 0:
        raise InsufficientStock(
            f"{batch.product.name} batch {batch.batch_number}: "
            f"{balance.quantity_base} available, {abs(base)} requested.",
            meta={
                "batch_id": str(batch.id),
                "available_base": balance.quantity_base,
                "requested_base": abs(base),
            },
        )

    movement = StockMovement(
        organization=organization,
        location=location,
        batch=batch,
        product=batch.product,
        status=status,
        kind=kind,
        quantity_base=base,
        balance_after_base=new_quantity,
        reason=reason,
        reference=reference,
        performed_by=performed_by,
        occurred_at=occurred_at,
        idempotency_key=idempotency_key,
    )
    try:
        with transaction.atomic():
            movement.save()
    except IntegrityError:
        # Two requests raced on the same key — the loser reports the
        # winner's result rather than a 500. An agent retrying after a
        # dropped connection must never see an error for work that
        # actually succeeded.
        existing = StockMovement.objects.get(
            organization=organization, idempotency_key=idempotency_key
        )
        return MovementResult(existing, existing.balance_after_base, replayed=True)

    balance.quantity_base = new_quantity
    balance.save(update_fields=["quantity_base", "updated_at"])

    return MovementResult(movement, new_quantity)


def _guard_cold_chain(batch: Batch, location: Location) -> None:
    """A cold-chain batch cannot be placed in an ambient location."""
    if (batch.cold_chain or batch.product.cold_chain) and not location.is_cold_capable:
        raise ColdChainViolation(
            f"{batch.product.name} requires 2–8°C. "
            f"{location.name} is {location.get_temperature_class_display()}.",
            meta={"location_id": str(location.id), "batch_id": str(batch.id)},
        )


def _lock_balance(
    *, organization: Organization, location: Location, batch: Batch, status: str
) -> StockBalance:
    """Row-lock the projection so concurrent issues cannot oversell."""
    balance = (
        StockBalance.objects.select_for_update()
        .filter(location=location, batch=batch, status=status)
        .first()
    )
    if balance is None:
        balance = StockBalance.objects.create(
            organization=organization,
            location=location,
            batch=batch,
            product=batch.product,
            status=status,
            quantity_base=0,
            expiry_date=batch.expiry_date,
        )
        balance = (
            StockBalance.objects.select_for_update().get(pk=balance.pk)
        )
    return balance


def allocate_fefo(
    *,
    organization: Organization,
    product: Product,
    location: Location,
    quantity: Quantity,
    as_of: date | None = None,
) -> list[Allocation]:
    """First **Expired**, First Out — not FIFO.

    Selects the nearest-expiry AVAILABLE batches with stock, skipping any
    that expire on or before ``as_of``. Returns allocations across one or
    more batches, or raises rather than partially allocating.

    Never select a batch by insertion order or primary key. Expiry loss is
    the direct cost of getting this wrong.
    """
    as_of = as_of or timezone.localdate()
    needed = quantity.base_value
    if needed <= 0:
        raise DomainError("Quantity must be positive.", code="invalid_quantity")

    candidates = (
        StockBalance.objects.filter(
            organization=organization,
            product=product,
            location=location,
            status=StockStatus.AVAILABLE,
            quantity_base__gt=0,
            expiry_date__gt=as_of,
        )
        .select_related("batch")
        .order_by("expiry_date", "batch__batch_number")
    )

    allocations: list[Allocation] = []
    remaining = needed
    for balance in candidates:
        if remaining <= 0:
            break
        take = min(balance.quantity_base, remaining)
        allocations.append(Allocation(balance.batch, take))
        remaining -= take

    if remaining > 0:
        available = needed - remaining
        raise InsufficientStock(
            f"{product.name}: {available} available, {needed} requested.",
            meta={
                "product_id": str(product.id),
                "available_base": available,
                "requested_base": needed,
            },
        )

    return allocations


@transaction.atomic
def issue_fefo(
    *,
    organization: Organization,
    product: Product,
    location: Location,
    quantity: Quantity,
    kind: str,
    performed_by: User | None = None,
    reference: str = "",
    reason: str = "",
    as_of: date | None = None,
    idempotency_key: str | None = None,
) -> list[MovementResult]:
    """Allocate FEFO and post the resulting movements.

    The common outbound path. A caller wanting a specific batch — an
    override — calls post_movement() directly with a reason, which is
    recorded on the movement.
    """
    allocations = allocate_fefo(
        organization=organization,
        product=product,
        location=location,
        quantity=quantity,
        as_of=as_of,
    )
    base_uom = product.base_uom
    results = []
    for index, allocation in enumerate(allocations):
        key = f"{idempotency_key}:{index}" if idempotency_key else None
        results.append(
            post_movement(
                organization=organization,
                location=location,
                batch=allocation.batch,
                kind=kind,
                quantity=Quantity(allocation.quantity_base, base_uom),
                performed_by=performed_by,
                reference=reference,
                reason=reason,
                idempotency_key=key,
            )
        )
    return results


def balance_for(
    *,
    organization: Organization,
    product: Product,
    location: Location | None = None,
    status: str = StockStatus.AVAILABLE,
) -> int:
    """Current balance in base units, from the projection."""
    qs = StockBalance.objects.filter(
        organization=organization, product=product, status=status
    )
    if location is not None:
        qs = qs.filter(location=location)
    return qs.aggregate(total=Sum("quantity_base"))["total"] or 0


def ledger_balance_for(
    *, organization: Organization, batch: Batch, location: Location, status: str
) -> int:
    """Balance recomputed from movements. The authoritative number."""
    return (
        StockMovement.objects.filter(
            organization=organization, batch=batch, location=location, status=status
        ).aggregate(total=Sum("quantity_base"))["total"]
        or 0
    )


@transaction.atomic
def rebuild_balances(*, organization: Organization) -> int:
    """Recompute every projection row from the ledger.

    The projection is disposable. If a balance ever looks wrong, rebuild
    and compare — never patch the projection, find the code path that
    wrote to it directly.

    Returns the number of rows that were wrong.
    """
    corrected = 0
    seen: set[tuple] = set()

    rows = (
        StockMovement.objects.filter(organization=organization)
        .values("location_id", "batch_id", "product_id", "status")
        .annotate(total=Sum("quantity_base"))
    )

    for row in rows:
        key = (row["location_id"], row["batch_id"], row["status"])
        seen.add(key)
        balance, created = StockBalance.objects.select_for_update().get_or_create(
            location_id=row["location_id"],
            batch_id=row["batch_id"],
            status=row["status"],
            defaults={
                "organization": organization,
                "product_id": row["product_id"],
                "quantity_base": row["total"],
                "expiry_date": Batch.objects.get(pk=row["batch_id"]).expiry_date,
            },
        )
        if not created and balance.quantity_base != row["total"]:
            balance.quantity_base = row["total"]
            balance.save(update_fields=["quantity_base", "updated_at"])
            corrected += 1

    # Projection rows with no movements behind them are stale by definition.
    stale = StockBalance.objects.filter(organization=organization).exclude(
        quantity_base=0
    )
    for balance in stale:
        if (balance.location_id, balance.batch_id, balance.status) not in seen:
            balance.quantity_base = 0
            balance.save(update_fields=["quantity_base", "updated_at"])
            corrected += 1

    return corrected


def expiring_batches(
    *, organization: Organization, within_days: int, as_of: date | None = None
):
    """Batches with stock expiring inside the window, nearest first."""
    as_of = as_of or timezone.localdate()
    horizon = as_of + timedelta(days=within_days)
    return (
        StockBalance.objects.filter(
            organization=organization,
            status=StockStatus.AVAILABLE,
            quantity_base__gt=0,
            expiry_date__lte=horizon,
        )
        .select_related("batch", "product", "location")
        .order_by("expiry_date")
    )
