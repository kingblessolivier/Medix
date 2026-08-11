"""Correlation id and tenant resolution.

See docs/25-request-pipeline.md.
"""

from __future__ import annotations

import uuid

from core.logging import set_correlation_id
from core.tenancy import set_current_organization


class CorrelationIdMiddleware:
    """Attach a correlation id to the request and to every log line."""

    HEADER = "HTTP_X_CORRELATION_ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        cid = request.META.get(self.HEADER) or str(uuid.uuid4())
        request.correlation_id = cid
        set_correlation_id(cid)
        response = self.get_response(request)
        response["X-Correlation-Id"] = cid
        return response


class TenantMiddleware:
    """Resolve the active organization from the authenticated user.

    Sets the context variable read by TenantManager. Requests without an
    organization see an empty queryset rather than everything.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        org_id = getattr(user, "organization_id", None) if user and user.is_authenticated else None
        set_current_organization(org_id)
        request.organization_id = org_id
        try:
            return self.get_response(request)
        finally:
            set_current_organization(None)
