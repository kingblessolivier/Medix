"""Eligibility, coverage, claims and remittance.

The exit criterion this exists to meet: a covered sale splits into co-pay
and claim correctly, and the claim reconciles on payment.

Two rules run through everything here.

**Money is split with integer arithmetic and the remainder goes to the
scheme, not the patient.** A percentage of a franc amount rarely divides
evenly, and rounding the patient's share up would overcharge a patient by
a franc on most lines — small, systematic, and in the wrong direction.

**Everything is resolved as of a date.** Coverage rules, contracts and
memberships are all effective-dated, so a dispensing from eight months
ago stays explainable under the rules that applied then.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.db import models, transaction
from django.utils import timezone

from core import audit, sequences
from core.exceptions import DomainError
from core.models import Organization, User
from documents import services as documents
from insurance.models import (
    CapitationReceipt,
    Claim,
    ClaimLine,
    ClaimPayment,
    ClaimStatus,
    CoverageRule,
    CoverageScope,
    Member,
    Scheme,
    SchemeContract,
)


class NotContracted(DomainError):
    default_code = "not_contracted"
    default_detail = "This pharmacy does not hold a contract with that scheme."


class NotEligible(DomainError):
    default_code = "not_eligible"
    default_detail = "This membership is not valid today."


class AlreadyClaimed(DomainError):
    default_code = "already_claimed"
    default_detail = "A claim already exists for this sale."


# --------------------------------------------------------------------------
# Eligibility
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Eligibility:
    """Whether cover applies, and if not, exactly why.

    The reason matters at the counter. "Not a member", "card expired" and
    "we are not on their panel" are three different conversations with
    the patient, and a single boolean makes all of them "computer says
    no".
    """

    member: Member | None
    contract: SchemeContract | None
    covered: bool
    reason: str

    @property
    def model(self) -> str:
        return self.contract.model if self.contract else ""

    def as_dict(self) -> dict:
        return {
            "covered": self.covered,
            "reason": self.reason,
            "member_number": self.member.member_number if self.member else "",
            "scheme": self.member.scheme.name if self.member else "",
            "model": self.model,
            "contract": str(self.contract.id) if self.contract else None,
        }


def contract_for(
    *, organization: Organization, scheme: Scheme, as_of: date | None = None
) -> SchemeContract | None:
    """The contract in force on `as_of`."""
    as_of = as_of or timezone.localdate()
    return (
        SchemeContract.objects.filter(
            organization=organization, scheme=scheme, effective_from__lte=as_of
        )
        .filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=as_of))
        .order_by("-effective_from")
        .first()
    )


def check_eligibility(
    *,
    organization: Organization,
    patient,
    scheme: Scheme | None = None,
    as_of: date | None = None,
) -> Eligibility:
    """Can this patient use cover here, today.

    Where the patient holds several memberships and none is named, the
    first valid one under a live contract wins. Choosing for them is
    better than refusing to choose, and the counter shows which was used.
    """
    as_of = as_of or timezone.localdate()
    if patient is None:
        return Eligibility(None, None, False, "No patient on this sale.")

    memberships = Member.objects.filter(
        organization=organization, patient=patient
    ).select_related("scheme")
    if scheme is not None:
        memberships = memberships.filter(scheme=scheme)

    memberships = list(memberships)
    if not memberships:
        return Eligibility(None, None, False, "No membership recorded.")

    expired = None
    uncontracted = None
    for membership in memberships:
        if not membership.is_valid(as_of=as_of):
            expired = membership
            continue
        contract = contract_for(
            organization=organization, scheme=membership.scheme, as_of=as_of
        )
        if contract is None or not contract.is_contracted:
            uncontracted = membership
            continue
        return Eligibility(membership, contract, True, "")

    if uncontracted is not None:
        # A real member who cannot use their cover here. Saying so is the
        # point: charging them in full without explanation is how a
        # pharmacy loses a customer it never knew it had.
        return Eligibility(
            uncontracted,
            None,
            False,
            f"Not on {uncontracted.scheme.name}'s panel. Patient pays in full.",
        )
    if expired is not None:
        return Eligibility(
            expired,
            None,
            False,
            f"{expired.scheme.name} membership expired.",
        )
    return Eligibility(None, None, False, "No valid membership.")


# --------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Split:
    gross: int
    covered: int
    patient: int
    basis_points: int
    rule: CoverageRule | None
    note: str = ""


#: Narrowest scope wins. Without an order, one scheme-wide rule would make
#: every product-specific rule unreachable.
_PRECEDENCE = [
    CoverageScope.PRODUCT,
    CoverageScope.CATEGORY,
    CoverageScope.LEGAL_STATUS,
    CoverageScope.ALL,
]


def resolve_rule(
    *, contract: SchemeContract, product, as_of: date | None = None
) -> CoverageRule | None:
    """The most specific rule in force for this product."""
    as_of = as_of or timezone.localdate()
    candidates = (
        CoverageRule.objects.filter(contract=contract, effective_from__lte=as_of)
        .filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=as_of))
        .order_by("-effective_from")
    )

    by_scope: dict[str, CoverageRule] = {}
    for rule in candidates:
        if rule.scope in by_scope:
            continue  # a later effective_from already won for this scope
        if rule.scope == CoverageScope.PRODUCT and rule.product_id != product.id:
            continue
        if rule.scope == CoverageScope.CATEGORY and rule.category_id != product.category_id:
            continue
        if (
            rule.scope == CoverageScope.LEGAL_STATUS
            and rule.legal_status != product.legal_status
        ):
            continue
        by_scope[rule.scope] = rule

    for scope in _PRECEDENCE:
        if scope in by_scope:
            return by_scope[scope]
    return None


def split_amount(
    *,
    contract: SchemeContract,
    product,
    amount: int,
    has_prescription: bool = False,
    as_of: date | None = None,
) -> Split:
    """Divide one line between the scheme and the patient.

    The remainder of the integer division goes to the **scheme**. A
    percentage of a franc amount rarely divides evenly, and rounding the
    patient's share up would overcharge them by a franc on most lines —
    small, systematic, and in the wrong direction.
    """
    rule = resolve_rule(contract=contract, product=product, as_of=as_of)

    if rule is None:
        return Split(amount, 0, amount, 0, None, "No coverage rule.")
    if rule.is_excluded:
        # Different from 0%: an excluded item raises no claim line at all.
        return Split(amount, 0, amount, 0, rule, "Excluded by the scheme.")
    if rule.requires_prescription and not has_prescription:
        return Split(
            amount, 0, amount, 0, rule, "Covered only against a prescription."
        )

    covered = amount * rule.coverage_basis_points // 10_000
    if rule.maximum_amount and covered > rule.maximum_amount:
        covered = rule.maximum_amount
    return Split(amount, covered, amount - covered, rule.coverage_basis_points, rule)


def price_sale(
    *,
    organization: Organization,
    sale,
    eligibility: Eligibility,
    as_of: date | None = None,
) -> dict:
    """What the patient pays and what the scheme is asked for.

    Computed against `line_total` — the amount after discount and tax,
    which is what the patient would otherwise hand over. Splitting the
    pre-tax figure would leave the tax unallocated and the two halves
    would not add up to the bill.
    """
    as_of = as_of or timezone.localdate()
    if not eligibility.covered or eligibility.contract is None:
        total = sum(line.line_total for line in sale.lines.all())
        return {
            "covered": False,
            "reason": eligibility.reason,
            "gross": total,
            "scheme_amount": 0,
            "patient_amount": total,
            "lines": [],
        }

    has_prescription = sale.prescription_id is not None and sale.prescription.is_verified

    lines = []
    scheme_total = 0
    patient_total = 0
    gross_total = 0
    for line in sale.lines.select_related("product"):
        split = split_amount(
            contract=eligibility.contract,
            product=line.product,
            amount=line.line_total,
            has_prescription=has_prescription,
            as_of=as_of,
        )
        gross_total += split.gross
        scheme_total += split.covered
        patient_total += split.patient
        lines.append({"line": line, "split": split})

    return {
        "covered": True,
        "reason": "",
        "model": eligibility.contract.model,
        "gross": gross_total,
        "scheme_amount": scheme_total,
        "patient_amount": patient_total,
        "lines": lines,
    }


# --------------------------------------------------------------------------
# Claims
# --------------------------------------------------------------------------


@transaction.atomic
def raise_claim(
    *,
    organization: Organization,
    sale,
    eligibility: Eligibility,
    performed_by: User,
    as_of: date | None = None,
) -> Claim | None:
    """Turn a covered sale into a claim, under fee-for-service only.

    **Capitation raises nothing**, and returning None rather than an
    empty claim is the point: the scheme has already paid for the period,
    so a claim would be asking twice. Utilisation is tracked against the
    capitation receipt instead.
    """
    if not eligibility.covered or eligibility.contract is None:
        return None
    if not eligibility.contract.claims_per_sale:
        return None
    if Claim.objects.filter(sale=sale).exists():
        raise AlreadyClaimed()

    as_of = as_of or timezone.localdate()
    priced = price_sale(
        organization=organization, sale=sale, eligibility=eligibility, as_of=as_of
    )
    if priced["scheme_amount"] <= 0:
        return None

    claim = Claim.objects.create(
        organization=organization,
        scheme=eligibility.member.scheme,
        contract=eligibility.contract,
        member=eligibility.member,
        sale=sale,
        claimed_amount=priced["scheme_amount"],
        patient_paid=priced["patient_amount"],
        currency=sale.currency,
        dispensed_on=as_of,
        # Frozen from the contract: renegotiating the window later must
        # not retroactively make this claim late.
        submit_by=as_of + timedelta(days=eligibility.contract.claim_window_days),
        created_by=performed_by,
    )

    for entry in priced["lines"]:
        split = entry["split"]
        if split.covered <= 0:
            continue
        ClaimLine.objects.create(
            claim=claim,
            sale_line=entry["line"],
            gross_amount=split.gross,
            covered_amount=split.covered,
            patient_amount=split.patient,
            coverage_basis_points=split.basis_points,
            rule=split.rule,
        )

    audit.record(
        action="insurance.claim.raised",
        subject=claim,
        actor=performed_by,
        after={
            "scheme": claim.scheme.name,
            "sale": sale.number,
            "claimed": claim.claimed_amount,
            "patient_paid": claim.patient_paid,
        },
        organization=organization,
    )
    return claim


@transaction.atomic
def submit_claim(*, claim: Claim, performed_by: User) -> Claim:
    """Send it. Numbered here, because a draft has nothing to quote."""
    if claim.status not in (ClaimStatus.DRAFT, ClaimStatus.REJECTED):
        raise DomainError("This claim has already been submitted.", code="already_submitted")
    if not claim.lines.exists():
        raise DomainError("Nothing to claim.", code="claim_empty")

    resubmission = claim.status == ClaimStatus.REJECTED
    if not claim.number:
        claim.number = sequences.next_number(claim.organization, "CLAIM")
    claim.status = ClaimStatus.RESUBMITTED if resubmission else ClaimStatus.SUBMITTED
    claim.submitted_at = timezone.now()
    claim.modified_by = performed_by
    claim.save(
        update_fields=["number", "status", "submitted_at", "modified_by", "modified_at"]
    )

    # The claim as it stood when it was sent. A resubmission after a
    # correction is a new version of the same number, not a new claim.
    documents.issue_claim(claim_record=claim, performed_by=performed_by)

    audit.record(
        action="insurance.claim.submitted",
        subject=claim,
        actor=performed_by,
        after={
            "number": claim.number,
            "resubmission": resubmission,
            "claimed": claim.claimed_amount,
        },
        organization=claim.organization,
    )
    return claim


@transaction.atomic
def record_response(
    *,
    claim: Claim,
    performed_by: User,
    allowed: dict[str, int] | None = None,
    rejections: dict[str, str] | None = None,
    reason: str = "",
    scheme_reference: str = "",
) -> Claim:
    """What the scheme actually allowed, line by line.

    Per line rather than in total, because a partial rejection is the
    common case and the pharmacy needs to know *which* line was refused
    to fix it. `allowed` maps claim-line id to the amount granted;
    anything in `rejections` is refused with its reason.
    """
    if claim.status not in (ClaimStatus.SUBMITTED, ClaimStatus.RESUBMITTED):
        raise DomainError("This claim is not awaiting a response.", code="not_submitted")

    allowed = allowed or {}
    rejections = rejections or {}
    total_allowed = 0

    for line in claim.lines.all():
        key = str(line.id)
        if key in rejections:
            line.is_rejected = True
            line.rejection_reason = rejections[key]
            line.allowed_amount = 0
        else:
            # Default to the full covered amount: a response that names
            # only the rejections is the common shape of a remittance.
            line.allowed_amount = int(allowed.get(key, line.covered_amount))
            line.is_rejected = False
            line.rejection_reason = ""
            total_allowed += line.allowed_amount
        line.save(update_fields=["allowed_amount", "is_rejected", "rejection_reason"])

    claim.allowed_amount = total_allowed
    claim.responded_at = timezone.now()
    claim.rejection_reason = reason
    claim.scheme_reference = scheme_reference
    claim.status = ClaimStatus.REJECTED if total_allowed == 0 else ClaimStatus.SUBMITTED
    claim.modified_by = performed_by
    claim.save(
        update_fields=[
            "allowed_amount", "responded_at", "rejection_reason",
            "scheme_reference", "status", "modified_by", "modified_at",
        ]
    )

    audit.record(
        action="insurance.claim.answered",
        subject=claim,
        actor=performed_by,
        after={
            "claimed": claim.claimed_amount,
            "allowed": claim.allowed_amount,
            "rejected_lines": len(rejections),
            "reason": reason,
        },
        organization=claim.organization,
    )
    return claim


@transaction.atomic
def record_claim_payment(
    *,
    claim: Claim,
    amount: int,
    performed_by: User,
    received_on: date | None = None,
    remittance_reference: str = "",
) -> ClaimPayment:
    """Money in from a scheme. Partial payment is normal."""
    if amount <= 0:
        raise DomainError("Payment must be positive.", code="non_positive_payment")
    if amount > claim.outstanding:
        raise DomainError(
            f"That is more than the {claim.outstanding:,} outstanding.",
            code="overpayment",
            meta={"outstanding": claim.outstanding},
        )

    payment = ClaimPayment.objects.create(
        organization=claim.organization,
        claim=claim,
        amount=amount,
        received_on=received_on or timezone.localdate(),
        remittance_reference=remittance_reference,
        created_by=performed_by,
    )
    claim.status = ClaimStatus.PAID if claim.outstanding == 0 else ClaimStatus.PART_PAID
    claim.save(update_fields=["status", "modified_at"])

    audit.record(
        action="insurance.claim.paid",
        subject=claim,
        actor=performed_by,
        after={
            "amount": amount,
            "outstanding": claim.outstanding,
            "remittance": remittance_reference,
        },
        organization=claim.organization,
    )
    return payment


@transaction.atomic
def write_off_claim(*, claim: Claim, performed_by: User, reason: str) -> Claim:
    """Give up on it, deliberately and with a reason.

    Separate from rejection: a rejected claim is still collectable and
    most are resubmitted. Writing off is the decision that it is not, and
    `docs/28` §12.4 counts it as one of the three leakages a pharmacy has
    to see rather than absorb.
    """
    if not reason.strip():
        raise DomainError("Give a reason for the write-off.", code="reason_required")

    claim.status = ClaimStatus.WRITTEN_OFF
    claim.reason = reason.strip()
    claim.modified_by = performed_by
    claim.save(update_fields=["status", "reason", "modified_by", "modified_at"])

    audit.record(
        action="insurance.claim.written_off",
        subject=claim,
        actor=performed_by,
        after={"number": claim.number, "amount": claim.outstanding, "reason": reason},
        organization=claim.organization,
    )
    return claim


# --------------------------------------------------------------------------
# Capitation
# --------------------------------------------------------------------------


def capitation_utilisation(
    *, organization: Organization, contract: SchemeContract, start: date, end: date
) -> dict:
    """Dispensed against paid, for a capitation contract.

    The question capitation actually asks. There is no receivable — the
    money arrived in advance — so the risk is the opposite one: having
    dispensed more than the period paid for.
    """
    from sales.models import SaleLine, SaleStatus

    dispensed = (
        SaleLine.objects.filter(
            sale__organization=organization,
            sale__status=SaleStatus.COMPLETED,
            sale__occurred_at__date__gte=start,
            sale__occurred_at__date__lte=end,
            sale__patient__memberships__scheme=contract.scheme,
        )
        .distinct()
        .aggregate(
            value=models.Sum("line_total"),
            cost=models.Sum(models.F("quantity_base") * models.F("unit_cost_base")),
        )
    )
    received = (
        CapitationReceipt.objects.filter(
            contract=contract, period_start__gte=start, period_end__lte=end
        ).aggregate(total=models.Sum("amount"))["total"]
        or 0
    )

    value = int(dispensed["value"] or 0)
    cost = int(dispensed["cost"] or 0)
    return {
        "scheme": contract.scheme.name,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "capitation_received": received,
        "dispensed_value": value,
        "dispensed_cost": cost,
        # Against cost, not retail: under capitation the pharmacy is
        # spending its own money on stock, and what it would have charged
        # is not what the arrangement cost it.
        "margin": received - cost,
        "over_utilised": cost > received,
    }


# --------------------------------------------------------------------------
# Receivables
# --------------------------------------------------------------------------

BUCKETS = [(0, 30), (31, 60), (61, 90), (91, None)]


def _label(low: int, high: int | None) -> str:
    return f"{low}-{high}" if high is not None else f"{low}+"


def receivables_by_scheme(
    *, organization: Organization, as_of: date | None = None
) -> dict:
    """What every scheme owes, aged.

    Rejected claims are counted separately rather than folded into the
    buckets: they are not late, they are refused, and mixing them makes
    the ageing look worse than it is while hiding work that could
    recover the money.
    """
    as_of = as_of or timezone.localdate()
    claims = Claim.objects.filter(
        organization=organization,
        status__in=[
            ClaimStatus.SUBMITTED,
            ClaimStatus.RESUBMITTED,
            ClaimStatus.PART_PAID,
            ClaimStatus.REJECTED,
        ],
    ).select_related("scheme")

    by_scheme: dict = {}
    buckets = {_label(low, high): 0 for low, high in BUCKETS}
    total = 0
    rejected_total = 0

    for claim in claims:
        entry = by_scheme.setdefault(
            str(claim.scheme_id),
            {
                "scheme": claim.scheme.name,
                "outstanding": 0,
                "rejected": 0,
                "claims": 0,
                **{_label(low, high): 0 for low, high in BUCKETS},
            },
        )
        entry["claims"] += 1

        if claim.status == ClaimStatus.REJECTED:
            entry["rejected"] += claim.claimed_amount
            rejected_total += claim.claimed_amount
            continue

        outstanding = claim.outstanding
        if outstanding <= 0:
            continue
        days = claim.days_overdue(as_of=as_of)
        label = next(
            _label(low, high) for low, high in BUCKETS if high is None or days <= high
        )
        entry[label] += outstanding
        entry["outstanding"] += outstanding
        buckets[label] += outstanding
        total += outstanding

    return {
        "as_of": as_of.isoformat(),
        "buckets": buckets,
        "total": total,
        "rejected_total": rejected_total,
        "schemes": sorted(by_scheme.values(), key=lambda row: -row["outstanding"]),
    }
