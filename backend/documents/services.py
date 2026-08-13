"""Issuing a document.

One entry point, `issue()`. It numbers, renders, hashes, stores and
audits. Nothing else creates a `Document`, because a document that
skipped numbering or auditing is not a document — it is a PDF.
"""

from __future__ import annotations

from django.core.files.base import ContentFile
from django.db import transaction

from core import audit, sequences
from core.exceptions import DomainError
from core.models import Organization, User
from documents import context as build
from documents.models import SEQUENCE_FOR, Document, DocumentKind
from documents.render import content_hash, render_html, render_pdf


class DocumentImmutable(DomainError):
    default_code = "document_immutable"
    default_detail = "An issued document cannot be changed. Reissue it instead."


@transaction.atomic
def issue(
    *,
    kind: str,
    subject,
    organization: Organization,
    context: dict,
    performed_by: User | None = None,
    number: str | None = None,
    supersedes: Document | None = None,
) -> Document:
    """Render and store one document.

    `number` is passed in when the subject already carries one — a
    delivery note is numbered when the shipment is dispatched, and
    issuing the paper must not allocate a second number for the same
    event. Everything else draws from `core.sequences`.

    A reissue keeps the number and increments the version, so the
    correction is visibly the same document rather than a new one that
    happens to look similar.
    """
    if supersedes is not None and supersedes.kind != kind:
        raise DomainError(
            "A document can only supersede one of its own kind.", code="kind_mismatch"
        )

    if supersedes is not None:
        number = supersedes.number
        version = supersedes.version + 1
    else:
        version = 1
        if number is None:
            number = sequences.next_number(organization, SEQUENCE_FOR[kind])

    context = {
        **context,
        "number": number,
        "version": version,
        "supersedes": f"{supersedes.number} v{supersedes.version}" if supersedes else "",
        "verification_reference": number,
    }

    document = Document(
        organization=organization,
        kind=kind,
        number=number,
        version=version,
        supersedes=supersedes,
        subject_type=subject._meta.label if subject is not None else "",
        subject_id=subject.pk if subject is not None else None,
        context=context,
        issued_by=performed_by,
        created_by=performed_by,
    )

    html = render_html(template=document.template, context=context)
    document.html = html
    document.sha256 = content_hash(html)

    pdf = render_pdf(html)
    document.save()
    if pdf is not None:
        document.pdf.save(f"{number}-v{version}.pdf", ContentFile(pdf), save=True)

    audit.record(
        action="documents.document.issued",
        subject=document,
        actor=performed_by,
        after={
            "kind": kind,
            "number": number,
            "version": version,
            "sha256": document.sha256,
            "subject": document.subject_type,
        },
        organization=organization,
    )
    return document


def latest(*, subject, kind: str) -> Document | None:
    """The current version of a document about `subject`."""
    return (
        Document.objects.filter(
            subject_type=subject._meta.label, subject_id=subject.pk, kind=kind
        )
        .order_by("-version")
        .first()
    )


# --------------------------------------------------------------------------
# The typed issuers. Each pairs a subject with its context builder.
# --------------------------------------------------------------------------


def issue_picking_ticket(*, shipment, performed_by: User | None = None) -> Document:
    return issue(
        kind=DocumentKind.PICKING_TICKET,
        subject=shipment,
        organization=shipment.organization,
        context=build.picking_ticket(shipment),
        performed_by=performed_by,
    )


def issue_delivery_note(*, shipment, performed_by: User | None = None) -> Document:
    # Numbered when the shipment dispatched. Issuing the paper must not
    # burn a second number for the same physical delivery.
    return issue(
        kind=DocumentKind.DELIVERY_NOTE,
        subject=shipment,
        organization=shipment.organization,
        context=build.delivery_note(shipment),
        performed_by=performed_by,
        number=shipment.number,
    )


def issue_invoice(*, invoice_record, performed_by: User | None = None) -> Document:
    kind = {
        "PROFORMA": DocumentKind.PROFORMA,
        "CREDIT_NOTE": DocumentKind.CREDIT_NOTE,
    }.get(invoice_record.kind, DocumentKind.TAX_INVOICE)
    return issue(
        kind=kind,
        subject=invoice_record,
        organization=invoice_record.organization,
        context=build.invoice(invoice_record),
        performed_by=performed_by,
        number=invoice_record.number,
    )


def issue_goods_receipt_note(*, receipt, performed_by: User | None = None) -> Document:
    return issue(
        kind=DocumentKind.GOODS_RECEIPT,
        subject=receipt,
        organization=receipt.organization,
        context=build.goods_receipt_note(receipt),
        performed_by=performed_by,
        number=receipt.number,
    )


def issue_controlled_transfer(*, transfer, performed_by: User | None = None) -> Document:
    return issue(
        kind=DocumentKind.CONTROLLED_TRANSFER,
        subject=transfer,
        organization=transfer.organization,
        context=build.controlled_transfer(transfer),
        performed_by=performed_by,
        number=transfer.number,
    )


def issue_claim(*, claim_record, performed_by: User | None = None) -> Document:
    """The claim as it stood when it was sent.

    A resubmission after a rejection is a **new version of the same
    number**, not a new claim — the scheme is looking at one claim that
    was corrected, and giving it a second number would make the two look
    like duplicate submissions.
    """
    previous = latest(subject=claim_record, kind=DocumentKind.CLAIM)
    return issue(
        kind=DocumentKind.CLAIM,
        subject=claim_record,
        organization=claim_record.organization,
        context=build.claim(claim_record),
        performed_by=performed_by,
        number=None if previous else claim_record.number,
        supersedes=previous,
    )
