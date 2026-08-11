"""Row-level tenancy.

The active organization lives in a context variable set by middleware and
is applied by ``TenantManager``. Using the unfiltered ``objects`` manager
in request-handling code is a review failure — see CONTRIBUTING.md.

See docs/24-database.md for the database-level backstop.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from django.db import models

_current_org: ContextVar[uuid.UUID | None] = ContextVar("medix_organization_id", default=None)
_bypass: ContextVar[bool] = ContextVar("medix_tenant_bypass", default=False)


def current_organization_id() -> uuid.UUID | None:
    return _current_org.get()


def set_current_organization(org_id: uuid.UUID | None) -> None:
    _current_org.set(org_id)


@contextmanager
def organization_scope(org_id: uuid.UUID | None) -> Iterator[None]:
    """Run a block scoped to one organization."""
    token = _current_org.set(org_id)
    try:
        yield
    finally:
        _current_org.reset(token)


@contextmanager
def tenant_bypass() -> Iterator[None]:
    """Escape hatch for regulator reads, migrations and background work.

    Never used in ordinary request handling. Cross-organization visibility
    belongs in explicit sharing endpoints, not here.
    """
    token = _bypass.set(True)
    try:
        yield
    finally:
        _bypass.reset(token)


class TenantQuerySet(models.QuerySet):
    pass


class TenantManager(models.Manager):
    """Filters every query by the active organization.

    With no organization set and no bypass active the manager returns an
    empty queryset rather than everything — failing closed.
    """

    def get_queryset(self) -> models.QuerySet:
        qs = TenantQuerySet(self.model, using=self._db)
        if _bypass.get():
            return qs
        org_id = current_organization_id()
        if org_id is None:
            return qs.none()
        return qs.filter(organization_id=org_id)
