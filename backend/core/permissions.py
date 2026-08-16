"""Tenant resolution at the DRF layer.

Django middleware runs *before* DRF authenticates, so `request.user` is
anonymous there for any token-authenticated request. Middleware alone
would silently scope every query to no organization — an empty result
rather than an error, which is the worst kind of failure.

DRF's ``initial()`` runs authentication and then permissions, so a
permission class is the first hook where the user is known. That is where
the active organization is set.

The middleware is kept for session-authenticated paths (Django admin).

See docs/02-architecture.md and docs/16-security.md.
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission

from core.tenancy import set_current_organization


class TenantScoped(BasePermission):
    """Bind the request to the authenticated user's organization.

    Returns False for an authenticated user with no organization rather
    than falling through to an unscoped query.
    """

    message = "No active organization."

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            set_current_organization(None)
            return False

        organization_id = getattr(user, "organization_id", None)
        set_current_organization(organization_id)
        request.organization_id = organization_id

        # A superuser without an organization may still reach unscoped
        # admin endpoints; ordinary users may not.
        return organization_id is not None or user.is_superuser
