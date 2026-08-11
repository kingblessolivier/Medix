"""Tenancy is on the mandatory-test list. See docs/15-testing.md.

The highest-value security test in the system: a missing filter here is a
reportable breach, not a bug.
"""

import pytest

from core.models import Branch, Organization
from core.tenancy import organization_scope, tenant_bypass

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_orgs():
    a = Organization.objects.create(name="Kigali Care Pharmacy", primary_kind="RETAIL")
    b = Organization.objects.create(name="ABC Wholesale", primary_kind="WHOLESALE")
    Branch.objects.create(organization=a, name="Kigali Main", code="KGL")
    Branch.objects.create(organization=b, name="ABC Depot", code="DEP")
    return a, b


class TestIsolation:
    def test_sees_only_own_rows(self, two_orgs):
        a, _ = two_orgs
        with organization_scope(a.id):
            names = list(Branch.tenant_objects.values_list("name", flat=True))
        assert names == ["Kigali Main"]

    def test_other_org_rows_are_invisible(self, two_orgs):
        a, b = two_orgs
        other = Branch.objects.get(organization=b)
        with organization_scope(a.id):
            assert not Branch.tenant_objects.filter(pk=other.pk).exists()

    def test_get_on_foreign_row_raises_does_not_exist(self, two_orgs):
        """404, never 403 — a 403 would confirm the record exists elsewhere."""
        a, b = two_orgs
        other = Branch.objects.get(organization=b)
        with organization_scope(a.id):
            with pytest.raises(Branch.DoesNotExist):
                Branch.tenant_objects.get(pk=other.pk)

    def test_fails_closed_with_no_organization(self, two_orgs):
        """No active organization returns nothing, not everything."""
        assert Branch.tenant_objects.count() == 0

    def test_unfiltered_manager_still_sees_all(self, two_orgs):
        """objects is for admin and background work only."""
        assert Branch.objects.count() == 2

    def test_bypass_sees_all(self, two_orgs):
        with organization_scope(two_orgs[0].id), tenant_bypass():
            assert Branch.tenant_objects.count() == 2

    def test_scope_is_restored_after_block(self, two_orgs):
        a, b = two_orgs
        with organization_scope(a.id):
            with organization_scope(b.id):
                assert Branch.tenant_objects.get().name == "ABC Depot"
            assert Branch.tenant_objects.get().name == "Kigali Main"


class TestAutoAssignment:
    def test_organization_is_set_from_scope_on_save(self, two_orgs):
        a, _ = two_orgs
        with organization_scope(a.id):
            branch = Branch(name="Remera", code="RMR")
            branch.save()
        assert branch.organization_id == a.id

    def test_explicit_organization_wins(self, two_orgs):
        a, b = two_orgs
        with organization_scope(a.id):
            branch = Branch(organization=b, name="Explicit", code="EXP")
            branch.save()
        assert branch.organization_id == b.id
