"""Sale rules.

`complete_sale()` is the transaction boundary for the whole counter
interaction: it gates on prescription, posts the ledger movements, writes
the controlled register, resolves tax, and allocates a number.

Nothing here trusts the caller for anything that matters. Totals are
recomputed, batch cost is read from the batch, tax is resolved against
effective-dated rules, and a POM line refuses to complete without a
verified prescription and a currently-registered pharmacist.

See docs/03-data-model.md, docs/06-compliance.md.
"""

from __future__ import annotations

import logging
from datetime import date

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from catalog.models import Product, TaxTreatment, UnitOfMeasure
from core import alerts, sequences
from core.exceptions import DomainError, PrescriptionRequired, RegistrationInvalid
from core.models import Organization, PharmacistRegistration, User
from core.quantity import Quantity
from inventory import services as inventory
from inventory.models import Location, MovementKind
from sales import clinical, interactions
from sales.models import (
    ControlledDeliveryEntry,
    Prescription,
    Sale,
    SaleLine,
    SaleStatus,
    Shift,
    TaxRule,
)


log = logging.getLogger(__name__)


class SaleNotDraft(DomainError):
    default_code = "sale_not_draft"
    default_detail = "This sale has already been completed."


class EmptySale(DomainError):
    default_code = "sale_empty"
    default_detail = "Add a product before completing."


class PatientAddressRequired(DomainError):
    default_code = "patient_address_required"
    default_detail = "A controlled substance needs the patient's address."


class NotSellable(DomainError):
    default_code = "not_sellable"
    default_detail = "This product cannot be sold in that unit."


# --------------------------------------------------------------------------
# Tax
# --------------------------------------------------------------------------


def resolve_tax_rate(
    *, organization: Organization, treatment: str, as_of: date
) -> int:
    """Rate in basis points, effective on ``as_of``.

    Preserves: a sale from last year stays explainable under last year's
    rules. Exempt and zero-rated both charge nothing, but they are not the
    same thing — input VAT is reclaimable only on the latter, which is why
    the treatment is recorded on the line and not just the rate.
    """
    if treatment in (TaxTreatment.EXEMPT, TaxTreatment.ZERO_RATED):
        return 0

    rule = (
        TaxRule.objects.filter(
            organization=organization,
            treatment=treatment,
            effective_from__lte=as_of,
        )
        .filter(models_effective_to_covers(as_of))
        .order_by("-effective_from")
        .first()
    )
    if rule is None:
        raise DomainError(
            f"No tax rule for {treatment} effective {as_of:%d %b %Y}.",
            code="tax_rule_missing",
            meta={"treatment": treatment, "as_of": as_of.isoformat()},
        )
    return rule.rate_basis_points


def models_effective_to_covers(as_of: date):
    from django.db.models import Q

    return Q(effective_to__isnull=True) | Q(effective_to__gte=as_of)


def compute_line_tax(subtotal_after_discount: int, rate_basis_points: int) -> int:
    """Tax on a line, in minor units, rounded half-up on integers only."""
    if rate_basis_points == 0:
        return 0
    return (subtotal_after_discount * rate_basis_points + 5000) // 10000


# --------------------------------------------------------------------------
# Building a sale
# --------------------------------------------------------------------------


@transaction.atomic
def start_sale(
    *,
    organization: Organization,
    branch,
    location: Location,
    cashier: User,
    till=None,
    shift: Shift | None = None,
) -> Sale:
    return Sale.objects.create(
        organization=organization,
        branch=branch,
        location=location,
        cashier=cashier,
        till=till,
        shift=shift,
        created_by=cashier,
    )


@transaction.atomic
def add_line(
    *,
    sale: Sale,
    product: Product,
    quantity: int,
    uom: UnitOfMeasure,
    unit_price: int,
    discount: int = 0,
    batch=None,
) -> list[SaleLine]:
    """Add a product, allocating batches by FEFO unless one is given.

    A request spanning two batches becomes two lines — each carries its own
    batch cost and traceability, and averaging them would destroy both.
    """
    if sale.status != SaleStatus.DRAFT:
        raise SaleNotDraft()
    if quantity <= 0:
        raise DomainError("Quantity must be positive.", code="invalid_quantity")
    if not uom.is_sellable:
        raise NotSellable(f"{product.name} cannot be sold by {uom.code.lower()}.")

    requested = Quantity(quantity, uom)
    as_of = timezone.localdate()

    if batch is not None:
        allocations = [(batch, requested.base_value)]
    else:
        allocations = [
            (a.batch, a.quantity_base)
            for a in inventory.allocate_fefo(
                organization=sale.organization,
                product=product,
                location=sale.location,
                quantity=requested,
                as_of=as_of,
            )
        ]

    rate = resolve_tax_rate(
        organization=sale.organization, treatment=product.tax_treatment, as_of=as_of
    )

    lines: list[SaleLine] = []
    for allocated_batch, base_qty in allocations:
        # Price is per the line's UoM; the allocation is in base units.
        line_quantity = base_qty // uom.factor_to_base
        remainder = base_qty % uom.factor_to_base
        if remainder:
            # The allocation does not divide into whole selling units, so
            # bill in base units rather than silently rounding.
            base_uom = product.base_uom
            line_uom = base_uom
            line_quantity = base_qty
            line_unit_price = unit_price // uom.factor_to_base
        else:
            line_uom = uom
            line_unit_price = unit_price

        subtotal = line_unit_price * line_quantity
        line_discount = discount if len(allocations) == 1 else 0
        taxable = subtotal - line_discount
        tax = compute_line_tax(taxable, rate)

        lines.append(
            SaleLine.objects.create(
                sale=sale,
                product=product,
                batch=allocated_batch,
                uom=line_uom,
                quantity=line_quantity,
                quantity_base=base_qty,
                unit_price=line_unit_price,
                line_subtotal=subtotal,
                discount=line_discount,
                tax_treatment=product.tax_treatment,
                tax_rate_basis_points=rate,
                tax_amount=tax,
                line_total=taxable + tax,
                legal_status=product.legal_status,
                unit_cost_base=allocated_batch.unit_cost_base,
            )
        )

    _recalculate(sale)
    return lines


def _recalculate(sale: Sale) -> None:
    """Totals are always derived from lines, never accepted from a client."""
    totals = sale.lines.aggregate(
        subtotal=Sum("line_subtotal"),
        discount=Sum("discount"),
        tax=Sum("tax_amount"),
        total=Sum("line_total"),
    )
    sale.subtotal = totals["subtotal"] or 0
    sale.discount_total = totals["discount"] or 0
    sale.tax_total = totals["tax"] or 0
    sale.total = totals["total"] or 0
    sale.save(update_fields=["subtotal", "discount_total", "tax_total", "total", "modified_at"])


# --------------------------------------------------------------------------
# Completing a sale
# --------------------------------------------------------------------------


def verify_pharmacist(user: User, *, as_of: date | None = None) -> PharmacistRegistration:
    """A dispensing pharmacist must hold a current council registration.

    An expired registration cannot verify a prescription or complete a
    prescription-only sale. Licence and registration expiry revoke
    capability automatically — that is the behaviour a regulator expects.
    """
    as_of = as_of or timezone.localdate()
    registration = (
        PharmacistRegistration.objects.filter(user=user, status="ACTIVE", expiry__gte=as_of)
        .order_by("-expiry")
        .first()
    )
    if registration is None:
        raise RegistrationInvalid(
            f"{user.get_full_name() or user.username} has no current pharmacist registration.",
            meta={"user_id": str(user.id)},
        )
    return registration


@transaction.atomic
def complete_sale(
    *,
    sale: Sale,
    performed_by: User,
    pharmacist: User | None = None,
    prescription: Prescription | None = None,
    idempotency_key: str | None = None,
    acknowledged: list[str] | None = None,
    clinical_reason: str = "",
) -> Sale:
    """Post the sale: gate, move stock, register, number, total.

    Preserves:
      - a prescription-only line never leaves the counter without a
        verified prescription and a registered pharmacist;
      - every controlled line produces exactly one register entry;
      - stock leaves through the ledger, never by decrement.
    """
    if sale.status in (SaleStatus.COMPLETED, SaleStatus.PENDING_PAYMENT):
        # Already posted. Re-completing must not move stock twice.
        return sale
    if sale.status != SaleStatus.DRAFT:
        raise SaleNotDraft()

    lines = list(sale.lines.select_related("product", "batch", "uom"))
    if not lines:
        raise EmptySale()

    prescription = prescription or sale.prescription
    needs_prescription = any(line.requires_prescription for line in lines)
    controlled = [line for line in lines if line.is_controlled]

    registration = None
    if needs_prescription:
        _gate_prescription(lines, prescription)
        if pharmacist is None:
            raise PrescriptionRequired(
                "A registered pharmacist must complete this sale.",
                code="pharmacist_required",
            )
        registration = verify_pharmacist(pharmacist)

    if controlled:
        _gate_controlled(prescription)

    # Clinical checks, where a patient is known. Warnings addressed to
    # the pharmacist: a hard stop would be worked around rather than
    # heeded, so what the system insists on is that the conflict was seen
    # and that the acknowledgement is on the record.
    #
    # Interaction checking is not among these — see sales/interactions.py
    # and the notice the counter prints.
    patient = prescription.patient if prescription is not None else sale.patient
    if patient is not None:
        found = clinical.for_dispensing(
            patient=patient, products=[line.product for line in lines]
        )
        found.extend(interactions.check(products=[line.product for line in lines]).alerts)
        alerts.enforce(
            found,
            organization=sale.organization,
            performed_by=pharmacist or performed_by,
            acknowledged=acknowledged or [],
            reason=clinical_reason,
        )

    # Stock leaves through the ledger. One movement per line, so each
    # batch's traceability stays intact.
    for index, line in enumerate(lines):
        key = f"{idempotency_key}:line:{index}" if idempotency_key else None
        inventory.post_movement(
            organization=sale.organization,
            location=sale.location,
            batch=line.batch,
            kind=MovementKind.SALE,
            quantity=Quantity(line.quantity_base, line.product.base_uom),
            performed_by=performed_by,
            reference=sale.number or "",
            idempotency_key=key,
        )

    for line in controlled:
        _write_controlled_entry(
            sale=sale,
            line=line,
            prescription=prescription,
            pharmacist=pharmacist,
            registration=registration,
        )

    # Cover is resolved before the number is allocated, so a claim and
    # the sale it belongs to are one transaction. Under capitation this
    # returns nothing — the scheme has already paid for the period, and
    # claiming as well would be asking twice.
    _raise_claim_if_covered(sale=sale, patient=patient, performed_by=performed_by)

    sale.number = sequences.next_number(sale.organization, "SALE")
    sale.pharmacist = pharmacist
    sale.prescription = prescription
    sale.modified_by = performed_by
    if idempotency_key:
        sale.idempotency_key = idempotency_key

    # Goods have left the counter. Whether the sale is *settled* depends on
    # what has actually been paid — a sale with nothing tendered is not
    # revenue, and day end would otherwise count it as such.
    from sales import payments as payment_services

    settled = payment_services.amount_settled(sale)
    if settled >= sale.total:
        sale.status = SaleStatus.COMPLETED
        sale.completed_at = timezone.now()
    else:
        sale.status = SaleStatus.PENDING_PAYMENT

    sale.save(
        update_fields=[
            "number",
            "pharmacist",
            "prescription",
            "status",
            "completed_at",
            "modified_by",
            "idempotency_key",
            "modified_at",
        ]
    )
    return sale


def _raise_claim_if_covered(*, sale, patient, performed_by) -> None:
    """Split a covered sale and raise its claim, where one applies.

    Failure here must not lose the dispensing. The goods have left the
    counter and the ledger has moved; a claim that could not be raised is
    recoverable paperwork, and refusing the sale over it would be the
    wrong trade.
    """
    if patient is None:
        return

    from insurance import services as insurance

    try:
        eligibility = insurance.check_eligibility(
            organization=sale.organization, patient=patient
        )
        if not eligibility.covered:
            return
        insurance.raise_claim(
            organization=sale.organization,
            sale=sale,
            eligibility=eligibility,
            performed_by=performed_by,
        )
    except Exception:
        log.exception("Could not raise a claim for %s", sale.id)


def _gate_prescription(lines: list[SaleLine], prescription: Prescription | None) -> None:
    """Blocks. Not a warning."""
    offending = [line.product.name for line in lines if line.requires_prescription]

    if prescription is None:
        raise PrescriptionRequired(
            f"{offending[0]} is prescription-only.",
            meta={"products": offending},
        )
    if not prescription.is_verified:
        raise PrescriptionRequired(
            "This prescription has not been verified.",
            code="prescription_unverified",
            meta={"prescription_id": str(prescription.id), "products": offending},
        )


def _gate_controlled(prescription: Prescription | None) -> None:
    """The register cannot be written without the patient's address."""
    if prescription is None:
        raise PrescriptionRequired("A controlled substance needs a prescription.")
    patient = prescription.patient
    if not patient.address.strip():
        raise PatientAddressRequired(
            "Law n° 03/2012 requires the patient's address for controlled dispensing.",
            meta={"patient_id": str(patient.id)},
        )


def _write_controlled_entry(
    *,
    sale: Sale,
    line: SaleLine,
    prescription: Prescription,
    pharmacist: User,
    registration: PharmacistRegistration,
) -> ControlledDeliveryEntry:
    previous = (
        ControlledDeliveryEntry.objects.filter(
            organization=sale.organization,
            substance_denomination=line.product.name,
        )
        .order_by("-entered_at")
        .first()
    )
    running = (previous.balance_after_base if previous else 0) - line.quantity_base

    return ControlledDeliveryEntry.objects.create(
        organization=sale.organization,
        sale_line=line,
        prescription=prescription,
        patient_name=prescription.patient.full_name,
        patient_address=prescription.patient.address,
        substance_denomination=line.product.name,
        schedule=line.product.controlled_schedule,
        quantity_base=line.quantity_base,
        uom_code=line.uom.code,
        dispensed_by=pharmacist,
        dispensed_by_council_number=registration.council_number,
        balance_after_base=running,
    )


@transaction.atomic
def verify_prescription(
    *, prescription: Prescription, pharmacist: User
) -> Prescription:
    """A registered pharmacist confirms. OCR never does.

    `ocr_extract` may have populated the fields, but verification is a
    human act attributed to a current registration.
    """
    registration = verify_pharmacist(pharmacist)

    prescription.verified_by = pharmacist
    prescription.verified_at = timezone.now()
    prescription.verified_by_council_number = registration.council_number
    prescription.status = "VERIFIED"
    prescription.modified_by = pharmacist
    prescription.save(
        update_fields=[
            "verified_by",
            "verified_at",
            "verified_by_council_number",
            "status",
            "modified_by",
            "modified_at",
        ]
    )
    return prescription
