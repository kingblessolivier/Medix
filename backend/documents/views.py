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

from documents.models import Document
from documents.serializers import DocumentSerializer


class DocumentFilter(filters.FilterSet):
    subject = filters.UUIDFilter(field_name="subject_id")

    class Meta:
        model = Document
        fields = ["kind", "number", "subject"]


class DocumentViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = DocumentSerializer
    filterset_class = DocumentFilter

    def get_queryset(self):
        return Document.tenant_objects.select_related("issued_by")

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
