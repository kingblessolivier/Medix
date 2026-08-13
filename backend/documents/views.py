"""Reading issued documents.

There is no create endpoint. Documents are issued by the service that
owns the event — dispatching raises the delivery note — because a
document nobody's workflow produced is a document nobody is accountable
for.
"""

from __future__ import annotations

from django.http import Http404, HttpResponse
from django_filters import rest_framework as filters
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.throttling import ScopedRateThrottle

from django.db import models

from documents.models import Document, DocumentKind
from documents.serializers import DocumentSerializer


class DocumentFilter(filters.FilterSet):
    subject = filters.UUIDFilter(field_name="subject_id")
    #: Everything one transaction produced, wherever it was attached.
    related = filters.UUIDFilter(method="_related")

    class Meta:
        model = Document
        fields = ["kind", "number", "subject", "related"]

    def _related(self, queryset, name, value):
        """Every document down this order's chain.

        A purchase order's paperwork is not attached to the order: the
        delivery note and picking ticket belong to the shipment, the
        invoice to the invoice, the GRN to the receipt. That is right —
        each document records the event that produced it — but a
        pharmacist looking at PO-2026-00001 means all of them.

        Ids only, and each read is tenant-scoped on the way in, so this
        widens what is *found* rather than what is visible.
        """
        from commerce.models import GoodsReceipt, Invoice, PurchaseOrder, Shipment

        order = PurchaseOrder.objects.filter(pk=value).first()
        if order is None:
            return queryset.filter(subject_id=value)

        subjects = {str(order.id)}
        subjects |= {
            str(pk) for pk in Shipment.objects.filter(order=order).values_list("id", flat=True)
        }
        subjects |= {
            str(pk) for pk in Invoice.objects.filter(order=order).values_list("id", flat=True)
        }
        subjects |= {
            str(pk)
            for pk in GoodsReceipt.objects.filter(order=order).values_list("id", flat=True)
        }
        return queryset.filter(subject_id__in=subjects)


class DocumentViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = DocumentSerializer
    filterset_class = DocumentFilter
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "documents"

    #: Addressed to the other side of the order, so readable by them.
    #: A picking ticket is not here on purpose: it is the depot telling
    #: its own staff which shelf to walk to, and it names locations the
    #: buyer has no business seeing.
    COUNTERPARTY_KINDS = [
        DocumentKind.DELIVERY_NOTE,
        DocumentKind.TAX_INVOICE,
        DocumentKind.PROFORMA,
        DocumentKind.CREDIT_NOTE,
        DocumentKind.DEBIT_NOTE,
    ]

    def get_queryset(self):
        """What this organization issued, plus what was issued to it.

        The second half is a deliberate, narrow crossing of the tenant
        line. It is bounded three ways: only these kinds, only documents
        attached to an order this organization placed, and read-only.
        A pharmacy that cannot open the delivery note for the boxes on
        its own counter is a pharmacy that keeps a paper file instead.
        """
        from commerce.models import GoodsReceipt, Invoice, PurchaseOrder, Shipment
        from core.tenancy import tenant_bypass

        mine = Document.tenant_objects.select_related("issued_by")

        organization = getattr(self.request.user, "organization", None)
        if organization is None:
            return mine

        orders = PurchaseOrder.objects.filter(organization=organization).values_list(
            "id", flat=True
        )
        if not orders:
            return mine

        subjects = {str(pk) for pk in orders}
        subjects |= {
            str(pk)
            for pk in Shipment.objects.filter(order_id__in=orders).values_list(
                "id", flat=True
            )
        }
        subjects |= {
            str(pk)
            for pk in Invoice.objects.filter(order_id__in=orders).values_list(
                "id", flat=True
            )
        }
        subjects |= {
            str(pk)
            for pk in GoodsReceipt.objects.filter(order_id__in=orders).values_list(
                "id", flat=True
            )
        }

        with tenant_bypass():
            addressed_to_me = list(
                Document.objects.filter(
                    subject_id__in=subjects, kind__in=self.COUNTERPARTY_KINDS
                )
                .exclude(organization=organization)
                .values_list("id", flat=True)
            )

        if not addressed_to_me:
            return mine
        return Document.objects.filter(
            models.Q(organization=organization) | models.Q(id__in=addressed_to_me)
        ).select_related("issued_by")

    @action(detail=True, methods=["get"])
    def preview(self, request, pk=None):
        """The stored HTML.

        The same bytes the PDF was rendered from, so preview and print
        cannot diverge — docs/18 treats a divergence as a bug, not a
        variation, and serving live-rendered HTML here would guarantee
        one the moment a product is renamed.
        """
        document = self.get_object()
        return HttpResponse(document.html, content_type="text/html; charset=utf-8")

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        document = self.get_object()
        if not document.pdf:
            raise Http404("This document has not been rendered to PDF.")
        response = HttpResponse(document.pdf.read(), content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="{document.number}-v{document.version}.pdf"'
        )
        return response
