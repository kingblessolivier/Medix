"""Invoices, payment terms and credit.

Three things here are policy rather than plumbing.

**A proforma is not a tax invoice.** A proforma asks for money before
goods move — a new pharmacy, or a controlled line. It creates no
receivable and it is not a debt. A tax invoice is. Collapsing them either
overstates what a depot is owed or lets goods leave against a document
that never demanded payment.

**Tax is frozen at issue.** The rate is resolved as-of the issue date and
written onto the line. An invoice is a legal document that must still
read the same in five years, so it must not be re-derived later from
whatever the rule table happens to say then.

**Credit is checked against what is actually outstanding**, including the
order being approved. Checking only historic debt lets a pharmacy sitting
at its limit place one more order every time, because the new one is
never part of the sum.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import models
from django.db import transaction
from django.utils import timezone

from commerce.models import (
    Invoice,
    InvoiceKind,
    InvoiceLine,
    InvoicePayment,
    InvoiceStatus,
    PurchaseOrder,
    TradingRelationship,
)
from core import audit, sequences
from core.exceptions import DomainError
from core.models import Organization, User
from documents import services as documents
from sales.services import compute_line_tax, resolve_tax_rate


class CreditLimitExceeded(DomainError):
    default_code = "credit_limit_exceeded"
    default_detail = "This pharmacy is at its credit limit."


class AlreadyIssued(DomainError):
    default_code = "invoice_already_issued"
    default_detail = "This invoice has been issued."


# --------------------------------------------------------------------------
# Credit
# --------------------------------------------------------------------------


def outstanding_for(*, supplier: Organization, customer: Organization) -> int:
    """Unpaid tax invoices, net of credit notes.

    A proforma is not a debt and is excluded. A credit note is a debt
    cancelled, so it comes off — a customer whose returned goods have
    been credited must not still be counted against their limit for
    goods they gave back.
    """
    invoices = Invoice.objects.filter(
        organization=supplier,
        customer=customer,
        kind=InvoiceKind.TAX,
        status__in=[InvoiceStatus.ISSUED, InvoiceStatus.PART_PAID],
    )
    owed = sum(invoice.outstanding for invoice in invoices)

    credited = (
        Invoice.objects.filter(
            organization=supplier,
            customer=customer,
            kind=InvoiceKind.CREDIT_NOTE,
            status__in=[InvoiceStatus.ISSUED, InvoiceStatus.PART_PAID, InvoiceStatus.PAID],
        ).aggregate(total=models.Sum("total"))["total"]
        or 0
    )
    return max(0, owed - credited)


def credit_position(
    *, supplier: Organization, customer: Organization, pending: int = 0
) -> dict:
    """Where this customer stands, including an order about to be approved.

    `pending` is the value of the order under consideration. Leaving it
    out is how a pharmacy already at its limit places one more order every
    time: each check passes because the new order is not counted yet.
    """
    relationship = TradingRelationship.objects.filter(
        organization=supplier, customer=customer
    ).first()
    limit = relationship.credit_limit if relationship else 0
    used = outstanding_for(supplier=supplier, customer=customer) + pending

    return {
        "limit": limit,
        "outstanding": used,
        "available": max(0, limit - used),
        "ratio": (used / limit) if limit else 0.0,
        # 80% is a warning, not a refusal — see docs/29-alerts.md.
        "near_limit": bool(limit) and used >= limit * 0.8,
    }


def assert_within_credit(
    *, supplier: Organization, customer: Organization, pending: int
) -> None:
    """Refuse an order that would take the customer past their limit.

    A depot with no limit recorded is trading on immediate payment, not
    extending unlimited credit, so a zero limit means the check does not
    apply rather than that anything is allowed.
    """
    position = credit_position(supplier=supplier, customer=customer, pending=pending)
    if position["limit"] <= 0:
        return
    if position["outstanding"] > position["limit"]:
        over = position["outstanding"] - position["limit"]
        raise CreditLimitExceeded(
            f"{customer.name} would be {over:,} over a {position['limit']:,} limit.",
            meta=position,
        )


# --------------------------------------------------------------------------
# Issuing
# --------------------------------------------------------------------------


@transaction.atomic
def build_invoice(
    *,
    order: PurchaseOrder,
    performed_by: User,
    kind: str = InvoiceKind.TAX,
) -> Invoice:
    """Draft an invoice for everything on an order.

    Priced from the order lines, not the listing: a listing that reprices
    after approval must not change what was already agreed.
    """
    lines = list(order.lines.select_related("product", "uom"))
    if not lines:
        raise DomainError("Nothing to invoice.", code="order_empty")

    invoice = Invoice.objects.create(
        organization=order.supplier,
        customer=order.organization,
        order=order,
        kind=kind,
        payment_terms_days=order.payment_terms_days,
        currency=order.currency,
        created_by=performed_by,
    )

    as_of = timezone.localdate()
    subtotal = 0
    tax_total = 0
    for line in lines:
        treatment = line.product.tax_treatment
        rate = resolve_tax_rate(
            organization=order.supplier, treatment=treatment, as_of=as_of
        )
        tax = compute_line_tax(line.line_total, rate)
        InvoiceLine.objects.create(
            invoice=invoice,
            product=line.product,
            uom=line.uom,
            description=f"{line.product.name} - {line.uom.name}",
            quantity=line.quantity,
            unit_price=line.unit_price,
            line_subtotal=line.line_total,
            tax_treatment=treatment,
            tax_rate_basis_points=rate,
            tax_amount=tax,
        )
        subtotal += line.line_total
        tax_total += tax

    invoice.subtotal = subtotal
    invoice.tax_total = tax_total
    invoice.total = subtotal + tax_total
    invoice.save(update_fields=["subtotal", "tax_total", "total"])
    return invoice


@transaction.atomic
def raise_credit_note(
    *,
    against: Invoice,
    amount: int,
    reason: str,
    performed_by: User,
    issued_on=None,
) -> Invoice:
    """Cancel part or all of an invoice, in the period it belongs to.

    `issued_on` is settable and defaults to today. A credit note for a
    November delivery agreed in January belongs to **November**: dating
    it today would move the correction into a period where the sale never
    happened, and both periods would then be wrong.

    That backdating is precisely why the period figures are computed
    rather than stored — see docs/28 §12.1.
    """
    if against.kind != InvoiceKind.TAX:
        raise DomainError(
            "Only a tax invoice can be credited.", code="not_creditable"
        )
    if against.status == InvoiceStatus.DRAFT:
        raise DomainError("That invoice has not been issued.", code="invoice_draft")
    if amount <= 0:
        raise DomainError("A credit note must be positive.", code="non_positive_credit")
    if not reason.strip():
        raise DomainError("Give a reason for the credit.", code="reason_required")

    already = (
        Invoice.objects.filter(against=against, kind=InvoiceKind.CREDIT_NOTE).aggregate(
            total=models.Sum("total")
        )["total"]
        or 0
    )
    if already + amount > against.total:
        raise DomainError(
            f"That would credit more than the {against.total:,} invoiced.",
            code="over_credit",
            meta={"invoiced": against.total, "already_credited": already},
        )

    # Tax comes off in the same proportion it went on. Crediting the
    # gross and leaving the VAT would overstate what the depot owes the
    # revenue authority for a sale that partly did not happen.
    tax = (
        (against.tax_total * amount) // against.total if against.total else 0
    )
    note = Invoice.objects.create(
        organization=against.organization,
        customer=against.customer,
        order=against.order,
        against=against,
        kind=InvoiceKind.CREDIT_NOTE,
        currency=against.currency,
        subtotal=amount - tax,
        tax_total=tax,
        total=amount,
        reason=reason.strip(),
        created_by=performed_by,
    )
    return issue_invoice(invoice=note, performed_by=performed_by, issued_on=issued_on)


@transaction.atomic
def issue_invoice(*, invoice: Invoice, performed_by: User, issued_on=None) -> Invoice:
    """Number it, date it, and start the clock on the terms."""
    if invoice.status != InvoiceStatus.DRAFT:
        raise AlreadyIssued()

    # `issued_on` is settable so a credit note can be dated into the
    # period it corrects rather than the period it was keyed in.
    today = issued_on or timezone.localdate()
    invoice.number = sequences.next_number(
        invoice.organization,
        {
            InvoiceKind.PROFORMA: "PROFORMA",
            InvoiceKind.CREDIT_NOTE: "CREDIT_NOTE",
        }.get(invoice.kind, "INVOICE"),
    )
    invoice.issued_on = today
    # Neither a proforma nor a credit note has a due date. One asks for
    # payment before goods move; the other cancels a debt rather than
    # creating one.
    invoice.due_on = (
        None
        if invoice.kind in (InvoiceKind.PROFORMA, InvoiceKind.CREDIT_NOTE)
        else today + timedelta(days=invoice.payment_terms_days)
    )
    invoice.status = InvoiceStatus.ISSUED
    invoice.modified_by = performed_by
    invoice.save(
        update_fields=[
            "number",
            "issued_on",
            "due_on",
            "status",
            "modified_by",
            "modified_at",
        ]
    )

    # Rendered against the invoice as it stands now. The stored context is
    # what makes the tax rate on this document survive a rule change.
    documents.issue_invoice(invoice_record=invoice, performed_by=performed_by)
    audit.record(
        action="commerce.invoice.issued",
        subject=invoice,
        actor=performed_by,
        after={
            "number": invoice.number,
            "kind": invoice.kind,
            "total": invoice.total,
            "currency": invoice.currency,
            "due_on": invoice.due_on,
        },
        organization=invoice.organization,
    )
    return invoice


@transaction.atomic
def record_payment(
    *,
    invoice: Invoice,
    amount: int,
    performed_by: User,
    method: str = "TRANSFER",
    reference: str = "",
) -> InvoicePayment:
    """Money received. Partial payment is normal, not an exception."""
    if invoice.status not in (InvoiceStatus.ISSUED, InvoiceStatus.PART_PAID):
        raise DomainError("This invoice is not awaiting payment.", code="invoice_not_open")
    if amount <= 0:
        raise DomainError("Payment must be positive.", code="non_positive_payment")
    if amount > invoice.outstanding:
        raise DomainError(
            f"That is more than the {invoice.outstanding:,} outstanding.",
            code="overpayment",
            meta={"outstanding": invoice.outstanding},
        )

    payment = InvoicePayment.objects.create(
        organization=invoice.organization,
        invoice=invoice,
        amount=amount,
        method=method,
        reference=reference,
        created_by=performed_by,
    )

    # `outstanding` queries the payments table rather than reading a
    # cached relation, so a caller holding a prefetched invoice cannot
    # leave a fully paid one sitting in PART_PAID.
    invoice.status = (
        InvoiceStatus.PAID if invoice.outstanding == 0 else InvoiceStatus.PART_PAID
    )
    invoice.save(update_fields=["status", "modified_at"])
    return payment


# --------------------------------------------------------------------------
# Ageing
# --------------------------------------------------------------------------

BUCKETS = [(0, 30), (31, 60), (61, 90), (91, None)]


def bucket_label(low: int, high: int | None) -> str:
    return f"{low}-{high}" if high is not None else f"{low}+"


def receivables_ageing(*, supplier: Organization, as_of=None) -> dict:
    """What is owed, by how late it is.

    The buckets are the ones a finance team already argues in — current,
    30, 60, 90+ — so the report needs no translation before it is used.
    """
    as_of = as_of or timezone.localdate()
    invoices = (
        Invoice.objects.filter(
            organization=supplier,
            kind=InvoiceKind.TAX,
            status__in=[InvoiceStatus.ISSUED, InvoiceStatus.PART_PAID],
        )
        .select_related("customer")
    )

    labels = [bucket_label(low, high) for low, high in BUCKETS]
    rows: dict[str, dict] = {}
    totals = {name: 0 for name in labels}

    for invoice in invoices:
        outstanding = invoice.outstanding
        if outstanding <= 0:
            continue
        overdue = invoice.days_overdue(as_of=as_of)
        key = next(
            bucket_label(low, high)
            for low, high in BUCKETS
            if overdue >= low and (high is None or overdue <= high)
        )
        row = rows.setdefault(
            str(invoice.customer_id),
            {"customer": invoice.customer.name, **{name: 0 for name in labels}, "total": 0},
        )
        row[key] += outstanding
        row["total"] += outstanding
        totals[key] += outstanding

    return {
        "as_of": as_of.isoformat(),
        "customers": sorted(rows.values(), key=lambda r: -r["total"]),
        "totals": totals,
        "total": sum(totals.values()),
    }
