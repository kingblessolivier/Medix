"""Recording expenses and writing off stock."""

from __future__ import annotations

from datetime import date

from django.db import transaction
from django.utils import timezone

from core import audit, sequences
from core.exceptions import DomainError
from core.models import Organization, User
from core.quantity import Quantity
from documents import services as documents
from documents.models import DocumentKind
from finance.models import DEFAULT_CATEGORIES, Expense, ExpenseCategory, WriteOff, WriteOffReason
from inventory import services as inventory
from inventory.models import Batch, Location, MovementKind, StockStatus


def seed_categories(organization: Organization) -> list[ExpenseCategory]:
    """Give an organization somewhere to put its costs.

    An expense screen with an empty category list gets filled with
    free text, and free text does not aggregate.
    """
    created = []
    for code, name, operating in DEFAULT_CATEGORIES:
        category, made = ExpenseCategory.objects.get_or_create(
            organization=organization,
            code=code,
            defaults={"name": name, "is_operating": operating},
        )
        if made:
            created.append(category)
    return created


@transaction.atomic
def record_expense(
    *,
    organization: Organization,
    category: ExpenseCategory,
    amount: int,
    incurred_on: date | None = None,
    performed_by: User | None = None,
    description: str = "",
    payee: str = "",
    reference: str = "",
    branch=None,
    currency: str = "RWF",
) -> Expense:
    """One cost, dated to when it was incurred.

    Not when it was keyed in. A November invoice entered in January
    belongs to November, or the period report records when somebody did
    their filing rather than what the business did.
    """
    if amount <= 0:
        raise DomainError("An expense must be positive.", code="non_positive_expense")

    expense = Expense.objects.create(
        organization=organization,
        category=category,
        branch=branch,
        amount=amount,
        currency=currency,
        incurred_on=incurred_on or timezone.localdate(),
        description=description,
        payee=payee,
        reference=reference,
        created_by=performed_by,
    )
    audit.record(
        action="finance.expense.recorded",
        subject=expense,
        actor=performed_by,
        after={
            "category": category.code,
            "amount": amount,
            "incurred_on": expense.incurred_on,
            "payee": payee,
        },
        organization=organization,
    )
    return expense


@transaction.atomic
def write_off(
    *,
    organization: Organization,
    batch: Batch,
    location: Location,
    quantity: Quantity,
    reason: str,
    performed_by: User,
    witness_name: str = "",
    witness_role: str = "",
    written_off_on: date | None = None,
) -> WriteOff:
    """Take stock off the shelf and record what it cost.

    Two things happen and both matter. The ledger loses the goods through
    `post_movement`, exactly as any other movement — there is no separate
    path that quietly decrements. And the value is captured **here, at
    this batch's cost**, rather than derived later: recomputing after a
    subsequent receipt would revalue history.

    A disposal is witnessed. The certificate is what an inspector asks
    for, and one with no witness is a note.
    """
    if reason not in WriteOffReason.values:
        raise DomainError(f"Unknown write-off reason: {reason}.", code="unknown_reason")

    movement_kind = (
        MovementKind.EXPIRY_WRITE_OFF
        if reason == WriteOffReason.EXPIRY
        else MovementKind.DISPOSAL
    )
    record = WriteOff.objects.create(
        organization=organization,
        batch=batch,
        location=location,
        reason=reason,
        quantity_base=quantity.base_value,
        unit_cost_base=batch.unit_cost_base,
        value=batch.unit_cost_base * quantity.base_value,
        written_off_on=written_off_on or timezone.localdate(),
        witness_name=witness_name,
        witness_role=witness_role,
        number=sequences.next_number(organization, "WRITE_OFF"),
        created_by=performed_by,
    )

    inventory.post_movement(
        organization=organization,
        location=location,
        batch=batch,
        kind=movement_kind,
        quantity=-quantity,
        performed_by=performed_by,
        reference=record.number,
        reason=record.get_reason_display(),
        # Expired stock is written off wherever it sits. Quarantined goods
        # are the common case — a batch is usually quarantined first and
        # destroyed after — so the status has to be passed through rather
        # than assumed available.
        status=StockStatus.AVAILABLE,
    )

    documents.issue(
        kind=DocumentKind.WRITE_OFF,
        subject=record,
        organization=organization,
        context=_write_off_context(record),
        performed_by=performed_by,
        number=record.number,
    )
    audit.record(
        action="finance.write_off.posted",
        subject=record,
        actor=performed_by,
        after={
            "number": record.number,
            "batch": batch.batch_number,
            "reason": reason,
            "quantity_base": record.quantity_base,
            "value": record.value,
        },
        organization=organization,
    )
    return record


def _write_off_context(record: WriteOff) -> dict:
    from documents import context as build

    context = build.base(
        doc_type="Inventory write-off certificate",
        number=record.number,
        issuer=build.party(record.organization),
        recipient={"name": "Rwanda FDA", "licence": "", "tin": "", "address": ""},
        status=record.get_reason_display(),
    )
    context.update(
        {
            "product": record.batch.product.name,
            "batch": record.batch.batch_number,
            "expiry": build.day(record.batch.expiry_date),
            "location": record.location.name,
            "quantity": build.quantity(
                record.quantity_base, record.batch.product.base_uom
            ),
            "unit_cost": build.money(record.unit_cost_base, record.currency),
            "value": build.money(record.value, record.currency),
            "currency": record.currency,
            "reason": record.get_reason_display(),
            "written_off_on": build.day(record.written_off_on),
            "witness_name": record.witness_name,
            "witness_role": record.witness_role,
        }
    )
    return context
