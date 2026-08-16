"""What the Assistant proposed, and what a person decided about it.

The rule in CLAUDE.md is that the Assistant never silently performs an
action that moves stock, money, or a regulated record. A rule enforced by
convention is a rule that will be skipped by the one client that forgets,
so it is enforced by shape instead: asking a question can *only* return a
row here, and the row does nothing until a second, explicit request
confirms it.

The row is kept either way. A proposal that was declined is as
interesting as one that was taken — it is the record of what the system
suggested and what the pharmacist thought of it.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import models
from django.utils import timezone

from core.models import TenantModel

#: A proposal is a snapshot of a situation. Confirming a stale one would
#: act on figures that have moved, so it lapses rather than lingering.
LIFETIME = timedelta(minutes=15)


class ProposalStatus(models.TextChoices):
    PROPOSED = "PROPOSED", "Awaiting confirmation"
    CONFIRMED = "CONFIRMED", "Confirmed"
    DECLINED = "DECLINED", "Declined"
    EXPIRED = "EXPIRED", "Expired"
    FAILED = "FAILED", "Failed"


class Proposal(TenantModel):
    """One suggested action, waiting for a person.

    `arguments` is a small dict of ids the handler resolves itself — the
    Assistant never stores a callable, a query, or anything the confirm
    step would have to trust.
    """

    question = models.TextField()
    action = models.CharField(max_length=40)
    arguments = models.JSONField(default=dict)

    #: What confirming does, in the words the pharmacist will read. Frozen
    #: at proposal time so the confirmation dialog and the audit record
    #: say the same thing.
    effect = models.CharField(max_length=200)

    status = models.CharField(
        max_length=12, choices=ProposalStatus.choices, default=ProposalStatus.PROPOSED
    )
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True)

    expires_at = models.DateTimeField()
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "assistant_proposal"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status"])]

    def __str__(self) -> str:
        return f"{self.action} ({self.get_status_display()})"

    @property
    def is_open(self) -> bool:
        return self.status == ProposalStatus.PROPOSED and timezone.now() < self.expires_at

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + LIFETIME
        super().save(*args, **kwargs)
