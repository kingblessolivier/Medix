"""Schemes, contracts, coverage and claims.

**Both reimbursement shapes are modelled, on purpose.** `docs/11-risks.md`
R3 records the open question — whether CBHI capitation applies to
contracted private pharmacies — and names this as its own mitigation:
`SchemeContract.model` carries the shape, so answering V3 later selects a
contract row rather than forcing a rewrite.

They are genuinely different, which is why one field is not enough on its
own:

* **Fee-for-service** — the pharmacy dispenses, claims per item, and is
  reimbursed retrospectively. Every covered sale raises a claim.
* **Capitation** — the scheme pays a fixed amount per member per period
  in advance. A covered sale raises **no claim at all**; what matters is
  utilisation against the money already received.

Coverage rules are effective-dated configuration on the same footing as
tax rules. A dispensing from eight months ago has to stay explainable
under the rules that applied then.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from core.models import BaseModel, TenantModel


class ReimbursementModel(models.TextChoices):
    """How the scheme actually pays. See the module docstring and R3."""

    FEE_FOR_SERVICE = "FEE_FOR_SERVICE", "Fee for service"
    CAPITATION = "CAPITATION", "Capitation"


class CapitationPeriod(models.TextChoices):
    MONTH = "MONTH", "Per member per month"
    QUARTER = "QUARTER", "Per member per quarter"


class Scheme(TenantModel):
    """An insurer or health-financing body.

    RSSB, CBHI, a private insurer. Held per organization rather than
    globally: a pharmacy's list of schemes is part of its commercial
    setup, and two pharmacies may know the same insurer by different
    contract terms.
    """

    name = models.CharField(max_length=200)
    code = models.CharField(max_length=30)
    contact_name = models.CharField(max_length=200, blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    contact_email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "insurance_scheme"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], name="uq_scheme_code"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class SchemeContract(TenantModel):
    """The terms this pharmacy trades under with one scheme.

    **Selective contracting is a fact about the pharmacy, not the
    patient.** A member of a scheme the pharmacy does not hold a contract
    with is still a valid member — they simply cannot use their cover
    here, and the counter must say that rather than silently charging
    them in full.

    Effective-dated, so last year's dispensing stays explainable under
    last year's terms.
    """

    scheme = models.ForeignKey(Scheme, on_delete=models.PROTECT, related_name="contracts")
    reference = models.CharField(max_length=60, blank=True)

    model = models.CharField(
        max_length=20,
        choices=ReimbursementModel.choices,
        default=ReimbursementModel.FEE_FOR_SERVICE,
    )
    #: Whether this pharmacy is on the scheme's panel at all.
    is_contracted = models.BooleanField(default=True)

    # -- fee for service ---------------------------------------------------
    #: Days from dispensing within which a claim must be submitted. A
    #: claim raised after this is refused by the scheme, so the system
    #: warns before it happens rather than after.
    claim_window_days = models.IntegerField(default=30)
    payment_terms_days = models.IntegerField(default=60)

    # -- capitation --------------------------------------------------------
    #: Paid per member per period, in minor units. Meaningless under
    #: fee-for-service and left at zero there.
    capitation_amount = models.BigIntegerField(default=0)
    capitation_period = models.CharField(
        max_length=10, choices=CapitationPeriod.choices, default=CapitationPeriod.MONTH
    )

    effective_from = models.DateField(default=timezone.localdate)
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "insurance_scheme_contract"
        ordering = ["scheme__name", "-effective_from"]
        indexes = [
            models.Index(fields=["organization", "scheme", "effective_from"]),
        ]

    def __str__(self) -> str:
        return f"{self.scheme.name} · {self.get_model_display()}"

    @property
    def claims_per_sale(self) -> bool:
        """Only fee-for-service raises a claim per dispensing."""
        return self.model == ReimbursementModel.FEE_FOR_SERVICE


class CoverageScope(models.TextChoices):
    """What a rule applies to, narrowest first when resolving.

    A rule on a product beats one on its category, which beats the
    scheme-wide default — otherwise a single blanket rule would make
    every specific one unreachable.
    """

    PRODUCT = "PRODUCT", "One product"
    CATEGORY = "CATEGORY", "A therapeutic category"
    LEGAL_STATUS = "LEGAL_STATUS", "A legal status"
    ALL = "ALL", "Everything"


class CoverageRule(TenantModel):
    """How much of an item this contract covers, and when it applied.

    `is_excluded` is separate from a zero percentage on purpose. "Not
    covered by this scheme" and "covered at 0%" reach the same number and
    are different statements to a patient — and only the first should
    stop a claim line being raised at all.
    """

    contract = models.ForeignKey(
        SchemeContract, on_delete=models.CASCADE, related_name="coverage_rules"
    )
    scope = models.CharField(max_length=15, choices=CoverageScope.choices)

    product = models.ForeignKey(
        "catalog.Product", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    category = models.ForeignKey(
        "catalog.Category", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    legal_status = models.CharField(max_length=12, blank=True)

    #: Basis points, so 8500 is 85%. Integer arithmetic throughout: a
    #: float share of a money amount is exactly the rounding that leaves
    #: a franc unaccounted for on every line.
    coverage_basis_points = models.IntegerField(default=0)
    #: Per dispensing, in minor units. Zero means no cap.
    maximum_amount = models.BigIntegerField(default=0)
    is_excluded = models.BooleanField(default=False)
    requires_prescription = models.BooleanField(default=False)

    effective_from = models.DateField(default=timezone.localdate)
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "insurance_coverage_rule"
        ordering = ["scope", "-effective_from"]
        indexes = [
            models.Index(fields=["contract", "scope", "effective_from"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(coverage_basis_points__gte=0)
                & models.Q(coverage_basis_points__lte=10000),
                name="ck_coverage_within_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_scope_display()} @ {self.coverage_basis_points / 100:g}%"


class Member(TenantModel):
    """A patient's membership of a scheme.

    Dated, because eligibility is a question about a moment: a card that
    expired last week does not cover a dispensing today, and one that
    expires tomorrow does.
    """

    patient = models.ForeignKey(
        "sales.Patient", on_delete=models.CASCADE, related_name="memberships"
    )
    scheme = models.ForeignKey(Scheme, on_delete=models.PROTECT, related_name="members")
    member_number = models.CharField(max_length=60)
    #: The employee or household head, where the patient is a dependant.
    principal_name = models.CharField(max_length=200, blank=True)

    valid_from = models.DateField(default=timezone.localdate)
    valid_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "insurance_member"
        ordering = ["-valid_from"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "scheme", "member_number"],
                name="uq_member_number",
            ),
        ]
        indexes = [models.Index(fields=["organization", "patient"])]

    def __str__(self) -> str:
        return f"{self.member_number} · {self.scheme.name}"

    def is_valid(self, *, as_of=None) -> bool:
        as_of = as_of or timezone.localdate()
        if not self.is_active or self.valid_from > as_of:
            return False
        return self.valid_to is None or self.valid_to >= as_of


class ClaimStatus(models.TextChoices):
    """A rejected claim is not the end of it.

    Most rejections are technical — a missing member number, a wrong
    code — and are resubmitted. Collapsing REJECTED into a terminal state
    would write off revenue that is still collectable, which is one of
    the three leakages `docs/28` §12.4 names.
    """

    DRAFT = "DRAFT", "Draft"
    SUBMITTED = "SUBMITTED", "Submitted"
    PART_PAID = "PART_PAID", "Partly paid"
    PAID = "PAID", "Paid"
    REJECTED = "REJECTED", "Rejected"
    RESUBMITTED = "RESUBMITTED", "Resubmitted"
    WRITTEN_OFF = "WRITTEN_OFF", "Written off"


class Claim(TenantModel):
    """What a scheme owes for one dispensing.

    Fee-for-service only. Under capitation the scheme has already paid,
    so no claim is raised — see `insurance.services.apply_cover`.
    """

    number = models.CharField(max_length=30, blank=True)
    scheme = models.ForeignKey(Scheme, on_delete=models.PROTECT, related_name="claims")
    contract = models.ForeignKey(
        SchemeContract, on_delete=models.PROTECT, related_name="claims"
    )
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="claims")
    sale = models.OneToOneField(
        "sales.Sale", on_delete=models.PROTECT, related_name="claim"
    )

    status = models.CharField(
        max_length=12, choices=ClaimStatus.choices, default=ClaimStatus.DRAFT
    )
    #: What was claimed, and what the scheme actually allowed. They differ
    #: whenever a line is rejected, and the gap is the thing a pharmacy
    #: needs to see.
    claimed_amount = models.BigIntegerField(default=0)
    allowed_amount = models.BigIntegerField(default=0)
    patient_paid = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="RWF")

    dispensed_on = models.DateField(default=timezone.localdate)
    submitted_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    #: Claim window from the contract, frozen when the claim is raised.
    submit_by = models.DateField(null=True, blank=True)

    rejection_reason = models.TextField(blank=True)
    scheme_reference = models.CharField(max_length=60, blank=True)

    class Meta:
        db_table = "insurance_claim"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "number"],
                condition=~models.Q(number=""),
                name="uq_claim_number",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["scheme", "status"]),
        ]

    def __str__(self) -> str:
        return self.number or f"draft claim {self.id}"

    @property
    def settled(self) -> int:
        return (
            ClaimPayment.objects.filter(claim=self).aggregate(
                total=models.Sum("amount")
            )["total"]
            or 0
        )

    @property
    def outstanding(self) -> int:
        """Against what was **allowed**, not what was claimed.

        Chasing the claimed figure after a partial rejection would be
        chasing money the scheme has already said it will not pay.
        """
        return max(0, self.allowed_amount - self.settled)

    def days_overdue(self, *, as_of=None) -> int:
        as_of = as_of or timezone.localdate()
        if self.submitted_at is None or self.outstanding == 0:
            return 0
        due = self.submitted_at.date() + timezone.timedelta(
            days=self.contract.payment_terms_days
        )
        return max(0, (as_of - due).days)


class ClaimLine(BaseModel):
    """One dispensed line, split into what the scheme owes and what the
    patient paid.

    Both halves are stored rather than one plus a percentage: the split
    was computed under a rule that may since have been superseded, and
    recomputing it later would restate what the patient was charged.
    """

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="lines")
    sale_line = models.OneToOneField(
        "sales.SaleLine", on_delete=models.PROTECT, related_name="claim_line"
    )

    gross_amount = models.BigIntegerField()
    covered_amount = models.BigIntegerField()
    patient_amount = models.BigIntegerField()

    #: The rule that produced the split, and its percentage, frozen.
    coverage_basis_points = models.IntegerField(default=0)
    rule = models.ForeignKey(
        CoverageRule, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    allowed_amount = models.BigIntegerField(default=0)
    is_rejected = models.BooleanField(default=False)
    rejection_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "insurance_claim_line"

    def __str__(self) -> str:
        return f"{self.sale_line} covered {self.covered_amount}"


class ClaimPayment(TenantModel):
    """A remittance from a scheme, against one claim.

    Schemes pay in batches, so `remittance_reference` is what ties a row
    here to the advice the scheme sent — without it, reconciling a
    payment covering forty claims is manual.
    """

    claim = models.ForeignKey(Claim, on_delete=models.PROTECT, related_name="payments")
    amount = models.BigIntegerField()
    received_on = models.DateField(default=timezone.localdate)
    remittance_reference = models.CharField(max_length=60, blank=True)

    class Meta:
        db_table = "insurance_claim_payment"
        ordering = ["-received_on"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="ck_claim_payment_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.amount} on {self.claim}"


class CapitationReceipt(TenantModel):
    """A capitation payment for a period, and the utilisation against it.

    Under capitation the money arrives before the dispensing, so the
    question is not "what are we owed" but "did we dispense more than we
    were paid for". That is a different report from a receivable and
    needs its own record — which is the whole reason `SchemeContract.model`
    exists rather than one workflow bent to fit both.
    """

    contract = models.ForeignKey(
        SchemeContract, on_delete=models.PROTECT, related_name="capitation_receipts"
    )
    period_start = models.DateField()
    period_end = models.DateField()
    members_covered = models.IntegerField(default=0)
    amount = models.BigIntegerField(default=0)
    received_on = models.DateField(null=True, blank=True)
    remittance_reference = models.CharField(max_length=60, blank=True)

    class Meta:
        db_table = "insurance_capitation_receipt"
        ordering = ["-period_start"]
        constraints = [
            models.UniqueConstraint(
                fields=["contract", "period_start"], name="uq_capitation_period"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.contract.scheme.name} {self.period_start:%b %Y}"
