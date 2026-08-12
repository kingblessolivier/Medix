"""Writing to the audit stream.

`core.AuditEvent` has been append-only since Phase 0, with the update and
delete grants revoked in production. This module is the only thing that
writes to it.

Rows are written **inside the caller's transaction**, deliberately. An
audit row for an action that rolled back would be a record of something
that never happened, which is worse than no record at all.

See docs/16-security.md and docs/03-data-model.md.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterator

from django.db import models

from core.logging import get_correlation_id
from core.models import AuditEvent, Organization, User
from core.tenancy import current_organization_id

#: Where the request came from. Set by middleware, empty in a shell or a
#: background job — which is itself worth recording, so it is not faked.
_request: ContextVar[dict] = ContextVar("medix_audit_request", default={})


def set_request_context(*, ip: str | None = None, user_agent: str = "") -> None:
    _request.set({"ip": ip, "user_agent": user_agent[:400]})


@contextmanager
def request_context(*, ip: str | None = None, user_agent: str = "") -> Iterator[None]:
    token = _request.set({"ip": ip, "user_agent": user_agent[:400]})
    try:
        yield
    finally:
        _request.reset(token)


def _jsonable(value: Any) -> Any:
    """Coerce a field value into something JSONField will accept.

    Money stays an integer, so nothing here touches it. Dates become ISO
    strings and UUIDs become their canonical form, both of which round
    trip.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        # Audit is a record, not arithmetic. A string keeps every digit.
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, models.Model):
        return str(value.pk)
    return str(value)


def snapshot(instance: models.Model, fields: list[str]) -> dict:
    """A JSON-safe dict of named fields, for `before` and `after`.

    Foreign keys are read through their `_id` attribute where one exists,
    so taking a snapshot never fires a query.
    """
    out: dict[str, Any] = {}
    for field in fields:
        attr = f"{field}_id"
        value = getattr(instance, attr) if hasattr(instance, attr) else getattr(instance, field, None)
        out[field] = _jsonable(value)
    return out


def record(
    *,
    action: str,
    subject: models.Model | None = None,
    actor: User | None = None,
    before: dict | None = None,
    after: dict | None = None,
    organization: Organization | None = None,
) -> AuditEvent:
    """Append one row to the audit stream.

    `action` is `<app>.<subject>.<verb>` — `commerce.order.confirmed`.
    Clients and reports branch on it, so it is a stable identifier rather
    than prose.

    The organization is resolved in falling order of reliability: the one
    passed in, the subject's own, the actor's, then the active tenant. A
    row with no organization is still written — an unattributed event is
    a finding, not a reason to drop the record.
    """
    subject_type = ""
    subject_id = None
    if subject is not None:
        subject_type = subject._meta.label
        subject_id = subject.pk

    org_id = None
    if organization is not None:
        org_id = organization.pk
    elif subject is not None and getattr(subject, "organization_id", None):
        org_id = subject.organization_id
    elif actor is not None and getattr(actor, "organization_id", None):
        org_id = actor.organization_id
    else:
        org_id = current_organization_id()

    ctx = _request.get() or {}

    return AuditEvent.objects.create(
        organization_id=org_id,
        actor=actor,
        action=action[:80],
        subject_type=subject_type[:80],
        subject_id=subject_id,
        before=_jsonable(before) if before is not None else None,
        after=_jsonable(after) if after is not None else None,
        ip=ctx.get("ip"),
        user_agent=ctx.get("user_agent", ""),
        correlation_id=get_correlation_id()[:64],
    )


def history(subject: models.Model, *, limit: int = 100) -> list[AuditEvent]:
    """Everything recorded about one record, newest first.

    Reads the unfiltered manager: `AuditEvent` is not a `TenantModel` and
    the caller has already proved access by holding the subject.

    Ordered by id as well as time. Two events written in the same
    millisecond tie on `occurred_at` — the clock is coarser than the code
    — and the database is then free to return them in either order. The
    id is a uuid7, so it sorts by creation and breaks the tie the way a
    reader expects.
    """
    return list(
        AuditEvent.objects.filter(
            subject_type=subject._meta.label, subject_id=subject.pk
        ).order_by("-occurred_at", "-id")[:limit]
    )
