"""Gap-free document numbering.

``SAL-2026-00982``. Allocated under a row lock held to commit, so
concurrent callers serialize per organization per type per year.

A gap in a fiscal or controlled-substance sequence is an audit finding.
This module is written and tested as if it were.

See docs/18-document-design.md.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from core.models import DocumentSequence, Organization

PREFIXES = {
    "SALE": "SAL",
    "PURCHASE_ORDER": "PO",
    "GOODS_RECEIPT": "GRN",
    "INVOICE": "INV",
    "CREDIT_NOTE": "CN",
    "IMPORT_REQUEST": "IR",
    "TRANSFER": "TRF",
    "ADJUSTMENT": "ADJ",
    "CLAIM": "CLM",
    "RFQ": "RFQ",
    "QUOTATION": "QUO",
    "DISPOSAL": "DSP",
    "RECALL": "REC",
}


class UnknownDocumentType(KeyError):
    pass


@transaction.atomic
def next_number(organization: Organization, type_code: str, *, year: int | None = None) -> str:
    """Allocate the next number for ``type_code``.

    Must be called inside the same transaction as the document it numbers.
    If that transaction rolls back the number is released, which is what
    keeps the sequence gap-free.
    """
    try:
        prefix = PREFIXES[type_code]
    except KeyError as exc:
        raise UnknownDocumentType(type_code) from exc

    year = year or timezone.localdate().year

    row, _ = DocumentSequence.objects.select_for_update().get_or_create(
        organization=organization, type_code=type_code, year=year,
        defaults={"next_value": 1},
    )
    value = row.next_value
    row.next_value = value + 1
    row.save(update_fields=["next_value"])

    return f"{prefix}-{year}-{value:05d}"


def peek(organization: Organization, type_code: str, *, year: int | None = None) -> int:
    """Next value without consuming it. For display only."""
    year = year or timezone.localdate().year
    row = DocumentSequence.objects.filter(
        organization=organization, type_code=type_code, year=year
    ).first()
    return row.next_value if row else 1
