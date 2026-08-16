"""Opening balances — the stock a pharmacy already has on the day it starts.

F20, and the requirement that decides whether anybody can actually begin
using Medix. A pharmacy adopting it is standing in a room with two
thousand boxes on the shelves, and until those exist in the ledger every
other feature is describing an empty shop.

**Opening is not receiving.** The distinction is the whole point of this
module. Recording go-live stock as `PURCHASE_RECEIPT` — which is what the
receive endpoint would do — says the pharmacy bought all of it on the
first day: it inflates that period's purchases, invents a supplier
relationship that never happened, and makes the first month's margin
meaningless. `OPENING` says "this was already here", which is the truth
and is what the ledger replay needs to reproduce balances from zero.

Cost still matters. An opening batch carries the price the pharmacy
actually paid for it, because every sale from that batch reports a margin
against it. A zero cost would make the first weeks of trading look like
pure profit, which is worse than no figure at all.

The whole import is one transaction. A half-loaded shelf is harder to
recover from than an empty one — the pharmacist cannot tell which rows
landed without counting the room again.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date

from django.db import transaction

from catalog.models import Product, UnitOfMeasure
from core.exceptions import DomainError
from core.models import Organization, User
from core.quantity import Quantity
from inventory import services
from inventory.models import Batch, Location, MovementKind, StockMovement


@dataclass
class Loaded:
    """What went in, and what was skipped and why."""

    batches: int = 0
    movements: int = 0
    base_units: int = 0
    #: Row number and reason, so a spreadsheet can be corrected and
    #: reloaded rather than argued with.
    skipped: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "batches": self.batches,
            "movements": self.movements,
            "base_units": self.base_units,
            "skipped": self.skipped,
        }


def _resolve_uom(product: Product, code: str) -> UnitOfMeasure:
    if not code:
        return product.base_uom
    unit = product.units.filter(code=code.upper()).first()
    if unit is None:
        raise DomainError(
            f"{product.name} has no unit '{code}'.", code="unknown_uom"
        )
    return unit


def _opening_key(*, location: Location, batch: Batch) -> str:
    """A stable key for one batch on one shelf, short enough to store."""
    seed = f"{location.id}:{batch.id}".encode()
    return f"opening:{hashlib.sha256(seed).hexdigest()[:40]}"


def _already_opened(*, organization: Organization, batch: Batch, location: Location) -> bool:
    """Has this batch ever moved in this room?

    The guard that stops a second import silently doubling the shelves.
    Scoped to batch and location rather than to the organization, so a
    pharmacy can open a new branch, or add a product it missed, without
    being told its go-live is over.
    """
    return StockMovement.objects.filter(
        organization=organization, batch=batch, location=location
    ).exists()


@transaction.atomic
def load_opening_balances(
    *,
    organization: Organization,
    location: Location,
    rows: list[dict],
    performed_by: User,
    counted_on: date | None = None,
) -> Loaded:
    """Establish what is on the shelves, as `OPENING` movements.

    Each row names a product, a batch number, an expiry, a quantity and
    what it cost. Everything else is derived.

    Refuses a batch that has already moved in this location rather than
    adding to it: a second run of the same spreadsheet would otherwise
    double the shelves, and nobody would notice until a stock take.

    One transaction. A partly loaded room is harder to recover from than
    an empty one, because the pharmacist cannot tell which rows landed
    without counting again.
    """
    if location.organization_id != organization.id:
        raise DomainError("That location belongs to somebody else.", code="not_yours")
    if not rows:
        raise DomainError("Nothing to load.", code="no_rows")

    loaded = Loaded()

    for index, row in enumerate(rows, start=1):
        product = Product.objects.filter(
            organization=organization, pk=row.get("product")
        ).first()
        if product is None:
            loaded.skipped.append({"row": index, "reason": "Unknown product"})
            continue

        number = str(row.get("batch_number", "")).strip()
        if not number:
            # A batch with no number cannot be recalled, traced or
            # reconciled against a delivery note. Refusing here is kinder
            # than accepting stock nobody can account for later.
            loaded.skipped.append({"row": index, "reason": "No batch number"})
            continue

        expiry = row.get("expiry_date")
        if not expiry:
            loaded.skipped.append({"row": index, "reason": "No expiry date"})
            continue

        try:
            quantity = int(row.get("quantity", 0))
        except (TypeError, ValueError):
            quantity = 0
        if quantity <= 0:
            loaded.skipped.append({"row": index, "reason": "Quantity must be positive"})
            continue

        uom = _resolve_uom(product, str(row.get("uom_code", "")))

        # The price actually paid, per base unit. Every sale from this
        # batch reports its margin against this number, so a zero here
        # makes the first weeks of trading look like pure profit.
        try:
            unit_cost = int(row.get("unit_cost_base", 0))
        except (TypeError, ValueError):
            unit_cost = 0

        batch, created = Batch.objects.get_or_create(
            organization=organization,
            product=product,
            batch_number=number,
            defaults={
                "expiry_date": expiry,
                "unit_cost_base": unit_cost,
                "cold_chain": product.cold_chain,
                "created_by": performed_by,
            },
        )
        if created:
            loaded.batches += 1
        elif _already_opened(
            organization=organization, batch=batch, location=location
        ):
            loaded.skipped.append(
                {"row": index, "reason": f"{number} is already on this shelf"}
            )
            continue

        held = Quantity(quantity, uom)
        services.post_movement(
            organization=organization,
            location=location,
            batch=batch,
            kind=MovementKind.OPENING,
            quantity=held,
            performed_by=performed_by,
            reason="Opening balance at go-live",
            reference=f"OPENING {counted_on or ''}".strip(),
            # Deterministic, so re-posting the same row is a no-op rather
            # than a second shelf. The batch guard above catches the
            # ordinary case; this catches a retried request. Hashed
            # because two uuids and a prefix overflow the column.
            idempotency_key=_opening_key(location=location, batch=batch),
        )
        loaded.movements += 1
        loaded.base_units += held.base_value

    if loaded.movements == 0:
        # Every row failed. Rolling back is right — a load that put
        # nothing on the shelves should not look like a success.
        raise DomainError(
            "No rows could be loaded.",
            code="nothing_loaded",
            meta={"skipped": loaded.skipped},
        )

    return loaded
