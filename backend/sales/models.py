"""Sales: tills, shifts, sales, prescriptions, the controlled register.

Three things here are regulatory, not conveniences:

- A prescription-only line **blocks** completion. Not a warning.
- OCR extracts; a registered pharmacist authorizes. `ocr_extract` is
  advisory data and never satisfies verification on its own.
- Every controlled delivery writes a statutory register entry carrying the
  patient's name and address, per Law n° 03/2012.

Tax is resolved **per line** against rules effective on the sale date.
Every pharmacy basket is mixed-treatment: medicines are exempt, cosmetics
and devices generally are not.

See docs/03-data-model.md and docs/06-compliance.md.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from catalog.models import Product, TaxTreatment, UnitOfMeasure
from core.models import AuditedModel, BaseModel, TenantModel


# --------------------------------------------------------------------------
# Tax — versioned configuration, never a constant
# --------------------------------------------------------------------------


class TaxRule(TenantModel):
    """Rate for a treatment, effective between two dates.

    A transaction from last year must remain explainable under last
    year's rules, so evaluation is always as-of the sale date.
    """

    treatment = models.CharField(max_length=10, choices=TaxTreatment.choices)
    rate_basis_points = models.IntegerField(
        help_text="Basis points: 1800 = 18%. Integer, so no float on the money path."
    )
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "sales_tax_rule"
        ordering = ["-effective_from"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rate_basis_points__gte=0), name="ck_tax_rate_nonneg"
            ),
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gt=models.F("effective_from")),
                name="ck_tax_rule_dates",
            ),
        ]
        indexes = [models.Index(fields=["organization", "treatment", "effective_from"])]

    def __str__(self) -> str:
        return f"{self.treatment} {self.rate_basis_points / 100:.1f}%"


# --------------------------------------------------------------------------
# Tills and shifts
# --------------------------------------------------------------------------


class Till(TenantModel):
    branch = models.ForeignKey("core.Branch", on_delete=models.PROTECT, related_name="tills")
    name = models.CharField(max_length=60)
    code = models.CharField(max_length=20)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "sales_till"
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], name="uq_till_code"),
        ]

    def __str__(self) -> str:
        return self.name


class ShiftStatus(models.TextChoices):
    OPEN = "OPEN", "Open"
    CLOSED = "CLOSED", "Closed"
    RECONCILED = "RECONCILED", "Reconciled"


class Shift(TenantModel):
    """One till's trading session. Day end reconciles against this."""

    till = models.ForeignKey(Till, on_delete=models.PROTECT, related_name="shifts")
    opened_by = models.ForeignKey("core.User", on_delete=models.PROTECT, related_name="+")
    opened_at = models.DateTimeField(default=timezone.now)
    closed_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    closed_at = models.DateTimeField(null=True, blank=True)

    opening_float = models.BigIntegerField(default=0)
    counted_cash = models.BigIntegerField(null=True, blank=True)
    variance = models.BigIntegerField(null=True, blank=True)
    variance_reason = models.TextField(blank=True)

    status = models.CharField(
        max_length=12, choices=ShiftStatus.choices, default=ShiftStatus.OPEN
    )

    class Meta:
        db_table = "sales_shift"
        ordering = ["-opened_at"]
        constraints = [
            # One open shift per till. Two would make the cash count meaningless.
            models.UniqueConstraint(
                fields=["till"], condition=models.Q(status="OPEN"), name="uq_one_open_shift"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.till.name} {self.opened_at:%d %b}"


# --------------------------------------------------------------------------
# People
# --------------------------------------------------------------------------


class Patient(TenantModel):
    """Sensitive personal data under Law 058/2021.

    Reads are audited, not just writes. Address is required only where a
    controlled substance is dispensed, and the POS states why when it asks.
    """

    full_name = models.CharField(max_length=200)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    national_id = models.CharField(max_length=32, blank=True)
    consent_given_at = models.DateTimeField(null=True, blank=True)
    consent_purpose = models.CharField(max_length=200, blank=True)

    # -- what a demographic check needs ------------------------------------
    #
    # Both optional, and absent means **not known** rather than not
    # applicable: a check that cannot run says so instead of passing. A
    # paediatric restriction silently skipped because nobody recorded a
    # birth date is the failure mode this comment exists to name.
    date_of_birth = models.DateField(null=True, blank=True)
    sex = models.CharField(
        max_length=1,
        blank=True,
        choices=[("F", "Female"), ("M", "Male")],
        help_text="Recorded only where a restriction depends on it.",
    )
    #: Set by the pharmacist, for the duration they judge relevant. Not
    #: inferred from anything, and cleared rather than left to expire.
    is_pregnant = models.BooleanField(null=True, blank=True)

    class Meta:
        db_table = "sales_patient"
        ordering = ["full_name"]

    def __str__(self) -> str:
        return self.full_name

    def age_years(self, *, as_of=None) -> int | None:
        """Whole years, or None when the birth date is not recorded."""
        if self.date_of_birth is None:
            return None
        as_of = as_of or timezone.localdate()
        years = as_of.year - self.date_of_birth.year
        if (as_of.month, as_of.day) < (
            self.date_of_birth.month,
            self.date_of_birth.day,
        ):
            years -= 1
        return years


class PatientAllergy(TenantModel):
    """An allergy a pharmacist recorded, against an active ingredient.

    Recorded against the **ingredient**, not a brand: a patient allergic
    to amoxicillin is allergic to it under every trade name, and matching
    on brand would miss the same drug sold as something else.

    Free text rather than a foreign key, because the allergen is often
    something this catalogue does not stock — a food, a preservative, a
    drug the pharmacy has never held. Normalised on save so "Penicillin"
    and "penicillin " match.
    """

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="allergies")
    allergen = models.CharField(max_length=120)
    #: Lowercased and stripped. Matching is done on this, never on the
    #: display form.
    allergen_normalised = models.CharField(max_length=120, editable=False)

    severity = models.CharField(
        max_length=12,
        choices=[
            ("MILD", "Mild"),
            ("MODERATE", "Moderate"),
            ("SEVERE", "Severe"),
            ("UNKNOWN", "Unknown"),
        ],
        default="UNKNOWN",
    )
    note = models.TextField(blank=True)
    recorded_on = models.DateField(default=timezone.localdate)

    class Meta:
        db_table = "sales_patient_allergy"
        ordering = ["allergen"]
        constraints = [
            models.UniqueConstraint(
                fields=["patient", "allergen_normalised"], name="uq_patient_allergen"
            ),
        ]

    def save(self, *args, **kwargs):
        self.allergen_normalised = self.allergen.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.patient.full_name}: {self.allergen}"


class Prescriber(TenantModel):
    full_name = models.CharField(max_length=200)
    council_number = models.CharField(max_length=60, blank=True)
    facility = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "sales_prescriber"
        ordering = ["full_name"]

    def __str__(self) -> str:
        return self.full_name


# --------------------------------------------------------------------------
# Prescriptions
# --------------------------------------------------------------------------


class PrescriptionStatus(models.TextChoices):
    PENDING = "PENDING", "Pending verification"
    VERIFIED = "VERIFIED", "Verified"
    REJECTED = "REJECTED", "Rejected"
    PARTIALLY_DISPENSED = "PARTIALLY_DISPENSED", "Partially dispensed"
    DISPENSED = "DISPENSED", "Dispensed"


class Prescription(TenantModel):
    number = models.CharField(max_length=60, blank=True)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="prescriptions")
    prescriber = models.ForeignKey(
        Prescriber, null=True, blank=True, on_delete=models.PROTECT, related_name="prescriptions"
    )
    issued_on = models.DateField(null=True, blank=True)

    #: Whatever OCR read. Advisory only — it never authorizes dispensing.
    ocr_extract = models.JSONField(null=True, blank=True)

    verified_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    #: Council number captured at the moment of verification, so the record
    #: stays truthful even if the registration later lapses or changes.
    verified_by_council_number = models.CharField(max_length=60, blank=True)

    status = models.CharField(
        max_length=24, choices=PrescriptionStatus.choices, default=PrescriptionStatus.PENDING
    )

    class Meta:
        db_table = "sales_prescription"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.number or str(self.id)

    @property
    def is_verified(self) -> bool:
        return self.status in (
            PrescriptionStatus.VERIFIED,
            PrescriptionStatus.PARTIALLY_DISPENSED,
            PrescriptionStatus.DISPENSED,
        )


# --------------------------------------------------------------------------
# Sales
# --------------------------------------------------------------------------


class SaleStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    PENDING_PAYMENT = "PENDING_PAYMENT", "Pending payment"
    COMPLETED = "COMPLETED", "Completed"
    VOIDED = "VOIDED", "Voided"


class Sale(TenantModel):
    number = models.CharField(max_length=30, blank=True)
    branch = models.ForeignKey("core.Branch", on_delete=models.PROTECT, related_name="sales")
    location = models.ForeignKey(
        "inventory.Location", on_delete=models.PROTECT, related_name="sales"
    )
    till = models.ForeignKey(Till, null=True, blank=True, on_delete=models.PROTECT, related_name="sales")
    shift = models.ForeignKey(
        Shift, null=True, blank=True, on_delete=models.PROTECT, related_name="sales"
    )

    cashier = models.ForeignKey("core.User", on_delete=models.PROTECT, related_name="+")
    #: Required once any line is POM or CONTROLLED.
    pharmacist = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    patient = models.ForeignKey(
        Patient, null=True, blank=True, on_delete=models.PROTECT, related_name="sales"
    )
    prescription = models.ForeignKey(
        Prescription, null=True, blank=True, on_delete=models.PROTECT, related_name="sales"
    )

    status = models.CharField(max_length=20, choices=SaleStatus.choices, default=SaleStatus.DRAFT)

    subtotal = models.BigIntegerField(default=0)
    tax_total = models.BigIntegerField(default=0)
    discount_total = models.BigIntegerField(default=0)
    total = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="RWF")

    occurred_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "sales_sale"
        ordering = ["-occurred_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "number"],
                condition=~models.Q(number=""),
                name="uq_sale_number",
            ),
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="uq_sale_idempotency",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "-occurred_at"]),
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["shift"]),
        ]

    def __str__(self) -> str:
        return self.number or f"draft {self.id}"

    @property
    def requires_pharmacist(self) -> bool:
        return any(line.requires_prescription for line in self.lines.all())


class SaleLine(BaseModel):
    """One product on a sale, allocated to one batch.

    A request spanning two batches becomes two lines, because each carries
    its own batch cost and traceability.
    """

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey("inventory.Batch", on_delete=models.PROTECT, related_name="+")
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")

    quantity = models.IntegerField(help_text="In the line's own unit of measure")
    quantity_base = models.BigIntegerField(help_text="Base units, for the ledger")

    unit_price = models.BigIntegerField(help_text="Per line UoM, minor units")
    line_subtotal = models.BigIntegerField()
    discount = models.BigIntegerField(default=0)

    #: Resolved per line: a basket of medicines and cosmetics carries two
    #: treatments on one document.
    tax_treatment = models.CharField(max_length=10, choices=TaxTreatment.choices)
    tax_rate_basis_points = models.IntegerField(default=0)
    tax_amount = models.BigIntegerField(default=0)
    line_total = models.BigIntegerField()

    #: Snapshot at the moment of sale — the product's status may change
    #: later, but what was dispensed today must stay explainable.
    legal_status = models.CharField(max_length=12)
    unit_cost_base = models.BigIntegerField(
        default=0, help_text="Landed cost per base unit, for margin"
    )

    class Meta:
        db_table = "sales_sale_line"
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="ck_line_qty_positive"),
            models.CheckConstraint(
                condition=models.Q(line_total__gte=0), name="ck_line_total_nonneg"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product.name} x{self.quantity}"

    @property
    def requires_prescription(self) -> bool:
        return self.legal_status in ("POM", "CONTROLLED")

    @property
    def is_controlled(self) -> bool:
        return self.legal_status == "CONTROLLED"


# --------------------------------------------------------------------------
# Controlled substances register — Law n° 03/2012
# --------------------------------------------------------------------------


class ControlledDeliveryEntry(BaseModel):
    """Statutory register. Append-only, reconcilable, reportable.

    The law requires every delivery of a narcotic or psychotropic to be
    registered immediately on the prescription, recording the patient's
    name and address and the denomination of the substance delivered.

    This is not a flag on a sale. It is its own record, and it is excluded
    from erasure workflows because statute requires retention.
    """

    organization = models.ForeignKey(
        "core.Organization", on_delete=models.PROTECT, related_name="+"
    )
    sale_line = models.OneToOneField(
        SaleLine, on_delete=models.PROTECT, related_name="controlled_entry"
    )
    prescription = models.ForeignKey(
        Prescription, on_delete=models.PROTECT, related_name="controlled_entries"
    )

    #: Captured explicitly, not derived — the register must stand alone.
    patient_name = models.CharField(max_length=200)
    patient_address = models.TextField()

    substance_denomination = models.CharField(max_length=200)
    schedule = models.CharField(max_length=20)
    quantity_base = models.BigIntegerField()
    uom_code = models.CharField(max_length=20)

    dispensed_by = models.ForeignKey("core.User", on_delete=models.PROTECT, related_name="+")
    dispensed_by_council_number = models.CharField(max_length=60)
    balance_after_base = models.BigIntegerField()

    entered_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "sales_controlled_delivery_entry"
        ordering = ["entered_at"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(patient_address=""), name="ck_controlled_address_required"
            ),
            models.CheckConstraint(
                condition=~models.Q(patient_name=""), name="ck_controlled_name_required"
            ),
        ]
        indexes = [models.Index(fields=["organization", "entered_at"])]

    def __str__(self) -> str:
        return f"{self.substance_denomination} {self.quantity_base}"

    def save(self, *args, **kwargs):
        if self.pk and ControlledDeliveryEntry.objects.filter(pk=self.pk).exists():
            raise RuntimeError("The controlled register is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("The controlled register is append-only.")


# --------------------------------------------------------------------------
# Payments
# --------------------------------------------------------------------------


class PaymentMethod(models.TextChoices):
    CASH = "CASH", "Cash"
    MOBILE_MONEY = "MOBILE_MONEY", "Mobile money"
    INSURANCE = "INSURANCE", "Insurance"
    CARD = "CARD", "Card"
    ACCOUNT = "ACCOUNT", "Account"


class PaymentStatus(models.TextChoices):
    """PENDING is a real state, not a transient one.

    Mobile money is request-to-pay plus callback: the customer confirms on
    their handset. It may resolve in seconds, time out, or need manual
    reconciliation.
    """

    PENDING = "PENDING", "Pending"
    CONFIRMED = "CONFIRMED", "Confirmed"
    FAILED = "FAILED", "Failed"
    TIMED_OUT = "TIMED_OUT", "Timed out"
    REVERSED = "REVERSED", "Reversed"


class Payment(AuditedModel):
    organization = models.ForeignKey(
        "core.Organization", on_delete=models.PROTECT, related_name="+"
    )
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="payments")
    method = models.CharField(max_length=16, choices=PaymentMethod.choices)
    provider = models.CharField(max_length=20, blank=True)
    amount = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="RWF")
    status = models.CharField(
        max_length=12, choices=PaymentStatus.choices, default=PaymentStatus.PENDING
    )
    provider_reference = models.CharField(max_length=80, blank=True)
    requested_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "sales_payment"
        ordering = ["requested_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="ck_payment_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.method} {self.amount}"

    @property
    def is_settled(self) -> bool:
        return self.status == PaymentStatus.CONFIRMED
