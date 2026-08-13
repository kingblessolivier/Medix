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


class TestEveryEndpointIsBound:
    """A view that overrides `permission_classes` can silently unbind itself.

    `TenantScoped` is where the active organization is set — middleware
    runs before DRF authenticates, so it is the first hook where the user
    is known (see core/permissions.py). Writing
    `permission_classes = [IsAuthenticated]` replaces the default pair
    and drops it. The failure is silent: `tenant_objects` then scopes to
    nothing and the endpoint returns an empty list or a 404 rather than
    an error, which is exactly the kind of bug that survives review.

    So the rule is checked here rather than trusted.
    """

    #: Deliberately unbound, each for a stated reason.
    EXEMPT = {
        # Liveness. Reached before login and by the load balancer.
        "health",
        # Provider callbacks arrive unauthenticated and are verified by
        # their own reference, not by a session.
        "PaymentCallbackView",
        # Issues the token in the first place.
        "TokenObtainPairView",
        "TokenRefreshView",
        # Creates the organization a tenant scope would be needed for.
        "RegisterPharmacyView",
        # The schema describes the API; it reads no tenant data.
        "SpectacularAPIView",
        "SpectacularSwaggerView",
    }

    def routes(self):
        from django.urls import get_resolver

        found = []
        for pattern in get_resolver().url_patterns:
            found.extend(self._walk(pattern))
        return found

    def _walk(self, pattern):
        from django.urls.resolvers import URLPattern, URLResolver

        if isinstance(pattern, URLResolver):
            out = []
            for child in pattern.url_patterns:
                out.extend(self._walk(child))
            return out
        if isinstance(pattern, URLPattern):
            view = getattr(pattern.callback, "cls", None) or pattern.callback
            return [(str(pattern.pattern), view)]
        return []

    def test_every_api_view_sets_the_active_organization(self):
        from core.permissions import TenantScoped

        unbound = []
        for route, view in self.routes():
            if not route.startswith("api/") and "api/v1" not in route:
                continue
            classes = getattr(view, "permission_classes", None)
            if classes is None:
                continue
            name = getattr(view, "__name__", str(view))
            if name in self.EXEMPT or route.rstrip("/").split("/")[-1] in self.EXEMPT:
                continue
            if not any(issubclass(c, TenantScoped) for c in classes):
                unbound.append(f"{name} ({route})")

        assert unbound == [], (
            "These views do not set the active organization: " + ", ".join(sorted(set(unbound)))
        )
