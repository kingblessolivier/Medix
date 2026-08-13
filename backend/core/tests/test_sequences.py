"""Document numbering must be gap-free.

A gap in a fiscal or controlled-substance sequence is an audit finding, so
this is tested as if it were one. See docs/18-document-design.md.
"""

import pytest
from django.db import transaction

from core import sequences
from core.models import DocumentSequence, Organization

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    return Organization.objects.create(name="Kigali Care Pharmacy", primary_kind="RETAIL")


class TestFormat:
    def test_shape(self, org):
        assert sequences.next_number(org, "SALE", year=2026) == "SAL-2026-00001"

    def test_prefix_per_type(self, org):
        assert sequences.next_number(org, "GOODS_RECEIPT", year=2026).startswith("GRN-")
        assert sequences.next_number(org, "IMPORT_REQUEST", year=2026).startswith("IR-")

    def test_unknown_type_raises(self, org):
        with pytest.raises(sequences.UnknownDocumentType):
            sequences.next_number(org, "NOT_A_TYPE")


class TestGapFree:
    def test_increments_without_gaps(self, org):
        numbers = [sequences.next_number(org, "SALE", year=2026) for _ in range(50)]
        assert numbers == [f"SAL-2026-{i:05d}" for i in range(1, 51)]

    def test_rollback_releases_the_number(self, org):
        """The defining property: an abandoned document leaves no gap."""
        first = sequences.next_number(org, "SALE", year=2026)

        with pytest.raises(RuntimeError):
            with transaction.atomic():
                sequences.next_number(org, "SALE", year=2026)
                raise RuntimeError("document abandoned")

        second = sequences.next_number(org, "SALE", year=2026)
        assert first == "SAL-2026-00001"
        assert second == "SAL-2026-00002"

    def test_separate_per_type(self, org):
        sequences.next_number(org, "SALE", year=2026)
        assert sequences.next_number(org, "INVOICE", year=2026) == "INV-2026-00001"

    def test_separate_per_year(self, org):
        sequences.next_number(org, "SALE", year=2026)
        assert sequences.next_number(org, "SALE", year=2027) == "SAL-2027-00001"

    def test_separate_per_organization(self, org):
        other = Organization.objects.create(name="ABC Wholesale", primary_kind="WHOLESALE")
        sequences.next_number(org, "SALE", year=2026)
        assert sequences.next_number(other, "SALE", year=2026) == "SAL-2026-00001"

    def test_row_is_created_once(self, org):
        for _ in range(5):
            sequences.next_number(org, "SALE", year=2026)
        assert DocumentSequence.objects.filter(organization=org, type_code="SALE").count() == 1


class TestPeek:
    def test_does_not_consume(self, org):
        assert sequences.peek(org, "SALE", year=2026) == 1
        assert sequences.peek(org, "SALE", year=2026) == 1
        assert sequences.next_number(org, "SALE", year=2026) == "SAL-2026-00001"
