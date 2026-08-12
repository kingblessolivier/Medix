"""Core models: tenancy, audit, organizations, licences, sequences.

See docs/03-data-model.md and docs/24-database.md.
"""

from __future__ import annotations

import uuid_utils.compat as uuid_utils
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

from core.tenancy import TenantManager, current_organization_id


def uuid7() -> uuid_utils.UUID:
    """Time-sortable UUID.

    Sortable keys keep index locality on append-only tables, and being
    client-generable lets the site agent create rows offline.
    """
    return uuid_utils.uuid7()


# --------------------------------------------------------------------------
# Abstract bases
# --------------------------------------------------------------------------


class BaseModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)

    class Meta:
        abstract = True


class AuditedModel(BaseModel):
    """Who touched this record, when, and why.

    Every user-mutable model inherits this. Do not add ad-hoc audit fields.
    """

    created_at = models.DateTimeField(default=timezone.now, editable=False)
    created_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    modified_at = models.DateTimeField(auto_now=True)
    modified_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    reason = models.TextField(blank=True)

    class Meta:
        abstract = True


class TenantModel(AuditedModel):
    """Scoped to one organization.

    ``tenant_objects`` filters by the active organization and is the only
    manager permitted in request-handling code. ``objects`` is unfiltered
    and reserved for admin and background work.
    """

    organization = models.ForeignKey(
        "core.Organization", on_delete=models.PROTECT, related_name="+"
    )

    objects = models.Manager()
    tenant_objects = TenantManager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.organization_id is None:
            org_id = current_organization_id()
            if org_id is not None:
                self.organization_id = org_id
        super().save(*args, **kwargs)


# --------------------------------------------------------------------------
# Organizations and licences
# --------------------------------------------------------------------------


class LicenceKind(models.TextChoices):
    """Rwanda FDA premises licence types.

    Retail and wholesale pharmacy are two licensed pharmacy types, not a
    pharmacy and some other business. See ADR-006.
    """

    RETAIL_PHARMACY = "RETAIL", "Retail pharmacy"
    WHOLESALE_PHARMACY = "WHOLESALE", "Wholesale pharmacy"
    IMPORTER = "IMPORTER", "Importer"
    DISTRIBUTOR = "DISTRIBUTOR", "Distributor"


class LicenceStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    SUSPENDED = "SUSPENDED", "Suspended"
    EXPIRED = "EXPIRED", "Expired"
    REVOKED = "REVOKED", "Revoked"


class Organization(AuditedModel):
    name = models.CharField(max_length=200)
    tin = models.CharField(max_length=20, blank=True, help_text="Taxpayer identification number")
    primary_kind = models.CharField(
        max_length=20, choices=LicenceKind.choices,
        help_text="Defaults the UI only. Capability comes from held licences.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "core_organization"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def holds(self, kind: str, *, at=None) -> bool:
        """True when any branch holds a valid licence of ``kind``."""
        at = at or timezone.localdate()
        return self.licences.filter(
            kind=kind, status=LicenceStatus.ACTIVE, expiry__gte=at
        ).exists()


class Branch(TenantModel):
    """A licensed premises."""

    name = models.CharField(max_length=200)
    code = models.CharField(max_length=20)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "core_branch"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], name="uq_branch_code"),
        ]

    def __str__(self) -> str:
        return self.name


class PremisesLicence(TenantModel):
    """Rwanda FDA premises licence. Expiry revokes capability.

    See docs/06-compliance.md.
    """

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="licences")
    kind = models.CharField(max_length=20, choices=LicenceKind.choices)
    number = models.CharField(max_length=60)
    issued_on = models.DateField()
    expiry = models.DateField()
    status = models.CharField(
        max_length=20, choices=LicenceStatus.choices, default=LicenceStatus.ACTIVE
    )
    issuing_authority = models.CharField(max_length=120, default="Rwanda FDA")

    class Meta:
        db_table = "core_premises_licence"
        constraints = [
            models.UniqueConstraint(fields=["organization", "number"], name="uq_licence_number"),
            models.CheckConstraint(
                condition=models.Q(expiry__gt=models.F("issued_on")),
                name="ck_licence_dates",
            ),
        ]
        indexes = [models.Index(fields=["organization", "kind", "expiry"])]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.number}"

    @property
    def is_valid(self) -> bool:
        return self.status == LicenceStatus.ACTIVE and self.expiry >= timezone.localdate()


# Organization.licences spans branches; declared here to keep the reverse
# accessor readable at the organization level.
Organization.add_to_class(
    "licences",
    property(lambda self: PremisesLicence.objects.filter(organization=self)),
)


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    organization = models.ForeignKey(
        Organization, null=True, blank=True, on_delete=models.PROTECT, related_name="users"
    )
    phone = models.CharField(max_length=20, blank=True)

    class Meta:
        db_table = "core_user"

    def __str__(self) -> str:
        return self.get_full_name() or self.username


class PharmacistRegistration(TenantModel):
    """National Pharmacy Council registration.

    Dispensing must be attributable to a registered professional; an
    expired registration cannot verify a prescription.
    """

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="registrations")
    council_number = models.CharField(max_length=60)
    issued_on = models.DateField()
    expiry = models.DateField()
    status = models.CharField(
        max_length=20, choices=LicenceStatus.choices, default=LicenceStatus.ACTIVE
    )

    class Meta:
        db_table = "core_pharmacist_registration"
        constraints = [
            models.UniqueConstraint(fields=["council_number"], name="uq_council_number"),
        ]

    def __str__(self) -> str:
        return self.council_number

    @property
    def is_valid(self) -> bool:
        return self.status == LicenceStatus.ACTIVE and self.expiry >= timezone.localdate()


# --------------------------------------------------------------------------
# Audit stream
# --------------------------------------------------------------------------


class AuditEvent(models.Model):
    """Append-only. Includes reads of patient data.

    No update or delete path exists; the grant is revoked in production.
    See docs/16-security.md.
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    organization = models.ForeignKey(
        Organization, null=True, on_delete=models.PROTECT, related_name="+"
    )
    actor = models.ForeignKey(User, null=True, on_delete=models.PROTECT, related_name="+")
    action = models.CharField(max_length=80)
    subject_type = models.CharField(max_length=80, blank=True)
    subject_id = models.UUIDField(null=True, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=400, blank=True)
    correlation_id = models.CharField(max_length=64, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "core_audit_event"
        indexes = [
            models.Index(fields=["organization", "occurred_at"]),
            models.Index(fields=["subject_type", "subject_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.subject_type}"


# --------------------------------------------------------------------------
# Document sequences
# --------------------------------------------------------------------------


class DocumentSequence(models.Model):
    """Gap-free numbering per organization, per type, per year.

    A gap in a fiscal or controlled-substance sequence is an audit finding,
    so a database SERIAL is unusable — it gaps on rollback.
    """

    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="+")
    type_code = models.CharField(max_length=20)
    year = models.IntegerField()
    next_value = models.BigIntegerField(default=1)

    class Meta:
        db_table = "core_document_sequence"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "type_code", "year"], name="uq_sequence"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.type_code}-{self.year}"


# --------------------------------------------------------------------------
# Alerts and quotas
#
# The models live here rather than beside their logic in `core/alerts.py`
# and `core/quotas.py`. Django registers a model when the module defining
# it is imported, and those modules are imported lazily by whichever
# service needs them — so the app registry ended up depending on import
# order, and a table Django did not know about broke the test flush.
# Behaviour stays in those modules; only the tables are declared here.
# --------------------------------------------------------------------------


class Severity(models.TextChoices):
    CRITICAL = "CRITICAL", "Critical"
    WARNING = "WARNING", "Warning"
    INFO = "INFO", "Info"


class AlertRule(TenantModel):
    """The number an alert fires at, with the dates it applied between.

    Ninety days before expiry and eighty percent of a credit limit are
    both policy decisions, and policy in this system is versioned
    configuration rather than a literal in a service. A pharmacy that
    tightens its short-dated window to 120 days must not retroactively
    make last quarter's decisions look negligent.
    """

    code = models.CharField(max_length=60)
    severity = models.CharField(max_length=10, choices=Severity.choices)
    #: Shape depends on the code — `{"days": 90}`, `{"percent": 80}`.
    #: A column per threshold would be mostly nulls and a migration per
    #: new alert.
    threshold = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)

    effective_from = models.DateField(default=timezone.localdate)
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "core_alert_rule"
        indexes = [models.Index(fields=["organization", "code", "effective_from"])]

    def __str__(self) -> str:
        return f"{self.code} from {self.effective_from}"


class AlertAcknowledgement(TenantModel):
    """Who accepted which warning, on which record, and why.

    An override nobody can trace is not a control, so this is written as
    a row *and* mirrored into the audit stream. Two records because they
    answer different questions: this one is "was it acknowledged", the
    audit event is "what happened here, in order".
    """

    code = models.CharField(max_length=60)
    severity = models.CharField(max_length=10, choices=Severity.choices)
    subject_type = models.CharField(max_length=80, blank=True)
    subject_id = models.UUIDField(null=True, blank=True)
    detail = models.TextField(blank=True)
    reason = models.TextField(blank=True)
    acknowledged_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    acknowledged_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "core_alert_acknowledgement"
        ordering = ["-acknowledged_at"]
        indexes = [models.Index(fields=["subject_type", "subject_id"])]

    def __str__(self) -> str:
        return f"{self.code} by {self.acknowledged_by}"


class QuotaPeriod(models.TextChoices):
    MONTH = "MONTH", "Calendar month"
    QUARTER = "QUARTER", "Calendar quarter"
    YEAR = "YEAR", "Calendar year"


class ControlledQuota(TenantModel):
    """How much of one schedule this organization may move in a period.

    Zero is not "none allowed" — it is "no quota recorded", and the check
    does not apply. A pharmacy with no quota on file is one the regulator
    has not capped, not one barred from trading.
    """

    schedule = models.CharField(max_length=20)
    period = models.CharField(
        max_length=10, choices=QuotaPeriod.choices, default=QuotaPeriod.MONTH
    )
    #: In base units of the product, summed across products in the
    #: schedule. Base units because a schedule spans dosage forms and
    #: "200 packs" means nothing across tablets and ampoules.
    limit_base = models.BigIntegerField(default=0)
    authority_reference = models.CharField(max_length=60, blank=True)

    effective_from = models.DateField(default=timezone.localdate)
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "core_controlled_quota"
        indexes = [
            models.Index(fields=["organization", "schedule", "effective_from"]),
        ]

    def __str__(self) -> str:
        return f"{self.schedule} {self.limit_base} per {self.get_period_display().lower()}"
