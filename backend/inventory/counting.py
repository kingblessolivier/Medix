"""Stock take — counting the room, and what to do about the difference.

F27. Two ideas do the work here.

**The count is not the correction.** A counter walks the shelves and
writes down what is there; the adjustment that reconciles the ledger is a
separate act, by somebody who can authorise it. Collapsing them would let
anybody with a clipboard rewrite the balance, which is exactly the
control a stock take exists to provide.

**Expected is snapshotted when the line is counted, not when it is
approved.** A pharmacy does not close to count. If the expected figure
were read at approval time, a sale made while the counter was three
aisles away would appear as a variance nobody can explain — and the
person explaining it would be the one who counted correctly.

Variance past the threshold needs a reason before approval, for the same
reason day end does: a discrepancy nobody wrote a sentence about is a
discrepancy nobody looked into.
"""

from __future__ import annotations

from django.db import models, transaction
from django.utils import timezone

from core import audit
from core.exceptions import DomainError
from core.models import BaseModel, Organization, TenantModel, User
from core.quantity import from_base

#: What a variance has to be *worth* before it needs explaining.
#:
#: Value rather than a unit count, because base units are capsules. Ten
#: capsules out of a thousand is the ordinary imprecision of counting a
#: shelf and demanding a written reason for it would train people to type
#: "counting error" on every line — which is worse than asking nothing,
#: since it buries the two vials of insulin that actually went missing.
#:
#: Scales by itself: cheap generics have to be a long way out before this
#: fires, and expensive stock barely at all. RWF minor units, matching the
#: day-end threshold, and the same kind of policy — both move to
#: effective-dated configuration when either does.
VARIANCE_VALUE_THRESHOLD = 5_000


class CountStatus(models.TextChoices):
    COUNTING = "COUNTING", "Being counted"
    SUBMITTED = "SUBMITTED", "Awaiting approval"
    APPROVED = "APPROVED", "Approved"
    CANCELLED = "CANCELLED", "Cancelled"


class StockCount(TenantModel):
    """One walk around one room."""

    location = models.ForeignKey(
        "inventory.Location", on_delete=models.PROTECT, related_name="counts"
    )
    reference = models.CharField(max_length=30, blank=True)
    status = models.CharField(
        max_length=12, choices=CountStatus.choices, default=CountStatus.COUNTING
    )
    counted_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    #: `AuditedModel` carries approved_by and approved_at.
    note = models.TextField(blank=True)

    class Meta:
        db_table = "inventory_stock_count"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status"])]

    def __str__(self) -> str:
        return self.reference or f"count {self.id}"


class StockCountLine(BaseModel):
    """One batch on one shelf, as found."""

    count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name="lines")
    batch = models.ForeignKey("inventory.Batch", on_delete=models.PROTECT, related_name="+")

    #: What the ledger said when this line was counted. Frozen here, so a
    #: sale made while the counter was elsewhere is not read as a
    #: discrepancy against the person who counted correctly.
    expected_base = models.BigIntegerField()
    counted_base = models.BigIntegerField()
    reason = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "inventory_stock_count_line"
        ordering = ["batch__expiry_date"]
        constraints = [
            models.UniqueConstraint(fields=["count", "batch"], name="uq_count_batch"),
        ]

    @property
    def variance_base(self) -> int:
        return self.counted_base - self.expected_base

    @property
    def variance_value(self) -> int:
        """What the difference is worth, at this batch's own cost."""
        return abs(self.variance_base) * self.batch.unit_cost_base

    @property
    def needs_a_reason(self) -> bool:
        return self.variance_value > VARIANCE_VALUE_THRESHOLD and not self.reason.strip()


# --------------------------------------------------------------------------
# Services
# --------------------------------------------------------------------------


@transaction.atomic
def open_count(*, organization: Organization, location, performed_by: User) -> StockCount:
    """Start counting a room.

    One at a time per room: two open counts would produce two sets of
    adjustments against the same shelves, and whichever was approved
    second would correct a figure the first had already corrected.
    """
    if StockCount.objects.filter(
        organization=organization,
        location=location,
        status__in=[CountStatus.COUNTING, CountStatus.SUBMITTED],
    ).exists():
        raise DomainError(
            f"{location.name} is already being counted.", code="count_in_progress"
        )

    from core import sequences

    count = StockCount.objects.create(
        organization=organization,
        location=location,
        reference=sequences.next_number(organization, "STOCK_COUNT"),
        counted_by=performed_by,
        created_by=performed_by,
    )
    audit.record(
        action="inventory.count.opened",
        subject=count,
        actor=performed_by,
        after={"location": location.name},
        organization=organization,
    )
    return count


@transaction.atomic
def record_count(
    *, count: StockCount, batch, counted_base: int, reason: str = ""
) -> StockCountLine:
    """Write down what is on the shelf for one batch.

    Recounting the same batch replaces the figure rather than adding a
    second line — a counter who miscounted and went back is the ordinary
    case, not an exception.
    """
    if count.status != CountStatus.COUNTING:
        raise DomainError("This count is closed.", code="count_closed")
    if counted_base < 0:
        raise DomainError("A count cannot be negative.", code="invalid_count")

    from inventory import services

    expected = services.ledger_balance_for(
        organization=count.organization,
        batch=batch,
        location=count.location,
        status="AVAILABLE",
    )

    line, _ = StockCountLine.objects.update_or_create(
        count=count,
        batch=batch,
        defaults={
            "expected_base": expected,
            "counted_base": counted_base,
            "reason": reason,
        },
    )
    return line


@transaction.atomic
def submit_count(*, count: StockCount, performed_by: User) -> StockCount:
    """Hand the sheet in. Nothing has moved yet."""
    if count.status != CountStatus.COUNTING:
        raise DomainError("This count is not being counted.", code="count_closed")
    if not count.lines.exists():
        raise DomainError("Count something first.", code="count_empty")

    unexplained = [
        line
        for line in count.lines.select_related("batch")
        if line.needs_a_reason
    ]
    if unexplained:
        raise DomainError(
            f"{len(unexplained)} lines are a long way out and have no reason.",
            code="variance_unexplained",
            meta={"lines": [str(line.id) for line in unexplained]},
        )

    count.status = CountStatus.SUBMITTED
    count.submitted_at = timezone.now()
    count.modified_by = performed_by
    count.save(update_fields=["status", "submitted_at", "modified_by", "modified_at"])
    audit.record(
        action="inventory.count.submitted",
        subject=count,
        actor=performed_by,
        after={"lines": count.lines.count()},
        organization=count.organization,
    )
    return count


def _assert_approver(count: StockCount, user: User) -> bool:
    """The counter does not approve their own sheet.

    Except where there is nobody else — the same judgement the purchase
    approval makes, and for the same reason: a control nobody can satisfy
    produces a second shared login rather than a second person.
    """
    if user.organization_id != count.organization_id:
        raise DomainError("Only this pharmacy can approve.", code="not_yours")
    if not count.counted_by_id or count.counted_by_id != user.id:
        return True

    colleagues = (
        User.objects.filter(organization_id=count.organization_id, is_active=True)
        .exclude(pk=user.pk)
        .exists()
    )
    if colleagues:
        raise DomainError(
            "A count cannot be approved by the person who made it.",
            code="not_approver",
        )
    return False


@transaction.atomic
def approve_count(*, count: StockCount, performed_by: User) -> dict:
    """Reconcile the ledger to what was found.

    This is the only place a count moves stock. One `ADJUSTMENT` per line
    that differs, through `post_movement` like everything else — there is
    no path here that writes a balance directly.
    """
    if count.status != CountStatus.SUBMITTED:
        raise DomainError("This count is not awaiting approval.", code="not_submitted")

    second_pair_of_eyes = _assert_approver(count, performed_by)

    from inventory import services
    from inventory.models import MovementKind

    adjusted = 0
    net_base = 0
    for line in count.lines.select_related("batch__product"):
        difference = line.variance_base
        if difference == 0:
            continue
        services.post_movement(
            organization=count.organization,
            location=count.location,
            batch=line.batch,
            kind=MovementKind.ADJUSTMENT,
            quantity=from_base(difference, line.batch.product.base_uom),
            performed_by=performed_by,
            reason=line.reason or f"Stock count {count.reference}",
            reference=count.reference,
            idempotency_key=f"count:{count.id}:{line.batch_id}"[:64],
        )
        adjusted += 1
        net_base += difference

    count.status = CountStatus.APPROVED
    count.approved_by = performed_by
    count.approved_at = timezone.now()
    count.modified_by = performed_by
    if not second_pair_of_eyes:
        count.note = "Self-approved: sole user of this pharmacy."
    count.save()

    audit.record(
        action="inventory.count.approved",
        subject=count,
        actor=performed_by,
        after={
            "adjusted": adjusted,
            "net_base": net_base,
            "self_approved": not second_pair_of_eyes,
        },
        organization=count.organization,
    )
    return {"adjusted": adjusted, "net_base": net_base, "reference": count.reference}


@transaction.atomic
def cancel_count(*, count: StockCount, performed_by: User, reason: str) -> StockCount:
    """Abandon it. Nothing was moved, so nothing is reversed."""
    if count.status == CountStatus.APPROVED:
        raise DomainError("An approved count cannot be cancelled.", code="already_approved")
    if not reason.strip():
        raise DomainError("Give a reason.", code="reason_required")

    count.status = CountStatus.CANCELLED
    count.reason = reason
    count.modified_by = performed_by
    count.save()
    return count
