"""Issued documents.

Roughly thirty-five types leave this system — a supplier reads the
purchase order, an inspector reads the disposal certificate. They are the
product's public face more often than the interface is.

See docs/18-document-design.md.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from core.models import TenantModel


class DocumentKind(models.TextChoices):
    """Each maps to a template and a sequence prefix.

    The value is also the template stem — `PICKING_TICKET` renders
    `docs/picking_ticket.html` — so adding a kind without a template
    fails loudly at issue rather than producing a blank page.
    """

    PICKING_TICKET = "PICKING_TICKET", "Picking ticket"
    DELIVERY_NOTE = "DELIVERY_NOTE", "Delivery note"
    TAX_INVOICE = "TAX_INVOICE", "Commercial tax invoice"
    PROFORMA = "PROFORMA", "Proforma invoice"
    GOODS_RECEIPT = "GOODS_RECEIPT", "Goods receipt note"
    CONTROLLED_TRANSFER = "CONTROLLED_TRANSFER", "Controlled substance transfer"
    CREDIT_NOTE = "CREDIT_NOTE", "Credit note"
    DEBIT_NOTE = "DEBIT_NOTE", "Debit note"
    WRITE_OFF = "WRITE_OFF", "Inventory write-off certificate"
    CLAIM = "CLAIM", "Insurance claim"


#: Which `core.sequences` code numbers each kind. Kept beside the choices
#: so a new document type cannot be added without deciding its numbering.
SEQUENCE_FOR = {
    DocumentKind.PICKING_TICKET: "PICKING_TICKET",
    DocumentKind.DELIVERY_NOTE: "DELIVERY_NOTE",
    DocumentKind.TAX_INVOICE: "INVOICE",
    DocumentKind.PROFORMA: "PROFORMA",
    DocumentKind.GOODS_RECEIPT: "GOODS_RECEIPT",
    DocumentKind.CONTROLLED_TRANSFER: "CONTROLLED_TRANSFER",
    DocumentKind.CREDIT_NOTE: "CREDIT_NOTE",
    DocumentKind.DEBIT_NOTE: "DEBIT_NOTE",
    DocumentKind.WRITE_OFF: "WRITE_OFF",
    DocumentKind.CLAIM: "CLAIM",
}


class Document(TenantModel):
    """One issued artifact, frozen at the moment it was issued.

    **`context` is the render input, stored.** A reissued invoice must
    show what it showed then. Re-rendering from live data would silently
    restate history — a product renamed, a tax rule superseded, an
    address corrected — and the reprint would no longer be the document
    that was signed.

    Immutable once issued. A correction is a new version pointing at what
    it supersedes, never an overwrite, so the earlier document remains
    readable and the amendment is visible rather than inferred.
    """

    kind = models.CharField(max_length=25, choices=DocumentKind.choices)
    number = models.CharField(max_length=30)

    #: What the document is about — the shipment, invoice, receipt or
    #: batch. Not a foreign key: documents span apps, and a nullable FK
    #: per type would be nine columns of which eight are always null.
    subject_type = models.CharField(max_length=80)
    subject_id = models.UUIDField(null=True, blank=True)

    context = models.JSONField(default=dict)
    html = models.TextField(blank=True)
    pdf = models.FileField(upload_to="documents/%Y/%m/", null=True, blank=True)
    #: Of the rendered HTML. Two issues of the same context must produce
    #: the same hash, which is what makes rendering testable as
    #: deterministic rather than merely plausible.
    sha256 = models.CharField(max_length=64, blank=True)

    version = models.IntegerField(default=1)
    supersedes = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="superseded_by"
    )

    issued_at = models.DateTimeField(default=timezone.now)
    issued_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        db_table = "documents_document"
        ordering = ["-issued_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "kind", "number", "version"],
                name="uq_document_number_version",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "kind"]),
            models.Index(fields=["subject_type", "subject_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.number} v{self.version}"

    @property
    def template(self) -> str:
        return f"docs/{self.kind.lower()}.html"

    @property
    def is_amended(self) -> bool:
        return self.superseded_by.exists()
