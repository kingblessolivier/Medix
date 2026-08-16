"""Document API shapes.

`context` and `html` are deliberately absent from the list shape. They
are large, and a list of thirty invoices carrying full render contexts
would move megabytes to draw a table of numbers.
"""

from __future__ import annotations

from rest_framework import serializers

from documents.models import Document


class DocumentSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    issued_by_name = serializers.SerializerMethodField()
    has_pdf = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            "id",
            "kind",
            "kind_label",
            "number",
            "version",
            "subject_type",
            "subject_id",
            "issued_at",
            "issued_by_name",
            "sha256",
            "has_pdf",
            "supersedes",
        ]

    def get_issued_by_name(self, document) -> str:
        return str(document.issued_by) if document.issued_by_id else ""

    def get_has_pdf(self, document) -> bool:
        return bool(document.pdf)
