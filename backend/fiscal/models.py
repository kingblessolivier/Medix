"""Fiscal records.

A completed sale must have a fiscal outcome: accepted, or visible in an
exception queue. It is never silently unfiscalized — that is the failure
RRA notices before we do.

Records are immutable once accepted. A correction is a credit note, never
an edit.

See docs/06-compliance.md §5.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from core.models import BaseModel, TenantModel


class FiscalStatus(models.TextChoices):
    QUEUED = "QUEUED", "Queued"
    ACCEPTED = "ACCEPTED", "Accepted"
    REJECTED = "REJECTED", "Rejected"
    FAILED = "FAILED", "Failed"


class FiscalRecord(BaseModel):
    """The fiscal outcome of one sale. Append-only once accepted."""

    organization = models.ForeignKey(
        "core.Organization", on_delete=models.PROTECT, related_name="+"
    )
    sale = models.OneToOneField(
        "sales.Sale", on_delete=models.PROTECT, related_name="fiscal_record"
    )

    status = models.CharField(
        max_length=12, choices=FiscalStatus.choices, default=FiscalStatus.QUEUED
    )
    backend = models.CharField(max_length=20, help_text="mock | vsdc")

    #: What the fiscal system returned. Its shape belongs to RRA, not us,
    #: so it is stored whole rather than picked apart into columns.
    receipt_number = models.CharField(max_length=60, blank=True)
    fiscal_signature = models.CharField(max_length=200, blank=True)
    device_id = models.CharField(max_length=60, blank=True)
    payload = models.JSONField(null=True, blank=True)

    error_code = models.CharField(max_length=60, blank=True)
    error_message = models.TextField(blank=True)
    attempts = models.IntegerField(default=0)

    submitted_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "fiscal_record"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["organization", "-created_at"]),
        ]

    def __str__(self) -> str:
        return self.receipt_number or f"{self.status} {self.sale_id}"

    @property
    def is_accepted(self) -> bool:
        return self.status == FiscalStatus.ACCEPTED

    @property
    def needs_attention(self) -> bool:
        """What the exception queue shows."""
        return self.status in (FiscalStatus.REJECTED, FiscalStatus.FAILED)

    def save(self, *args, **kwargs):
        if self.pk:
            existing = FiscalRecord.objects.filter(pk=self.pk).first()
            if existing and existing.status == FiscalStatus.ACCEPTED:
                raise RuntimeError(
                    "An accepted fiscal record is immutable. Issue a credit note."
                )
        super().save(*args, **kwargs)
