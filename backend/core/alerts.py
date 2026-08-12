"""Alerts, and the acknowledgement that makes them mean something.

Three severities, and **severity is behaviour, not decoration**:

* `CRITICAL` raises. The transaction did not happen. There is no
  "proceed anyway" — a case that genuinely needs an override is a
  warning with an authorised approver, not a critical.
* `WARNING` refuses unless the caller passes the code back in
  `acknowledged`. If acknowledgement were a UI convention the API would
  remain the real boundary and any client could skip it; making the
  service refuse turns it into a fact.
* `INFO` never interrupts.

Thresholds live in `AlertRule` with effective dates, not as constants
here. Ninety days and eighty percent are policy, and `CLAUDE.md` rule 4
covers policy: an alert that fired in March must stay explainable under
March's threshold.

See docs/29-alerts.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from django.db import models
from django.utils import timezone

from core import audit
from core.exceptions import DomainError
from core.models import (
    AlertAcknowledgement,
    AlertRule,
    Organization,
    Severity,
    User,
)


@dataclass(frozen=True)
class Alert:
    """One thing worth saying, about one record.

    `code` is the stable identifier — clients branch on it, thresholds
    are configured against it, acknowledgements are recorded under it.
    `title` and `detail` are prose and may be reworded freely.
    """

    code: str
    severity: str
    title: str
    detail: str = ""
    subject_type: str = ""
    subject_id: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.severity == Severity.CRITICAL

    @property
    def needs_acknowledgement(self) -> bool:
        return self.severity == Severity.WARNING

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "meta": self.meta,
        }


def about(subject, **kwargs) -> Alert:
    """An alert bound to a record, so the interface can attach it."""
    return Alert(
        subject_type=subject._meta.label if subject is not None else "",
        subject_id=str(subject.pk) if subject is not None else "",
        **kwargs,
    )


# --------------------------------------------------------------------------
# Effective-dated thresholds
# --------------------------------------------------------------------------


#: Used when an organization has configured nothing. Every value here is
#: also the seed for a real `AlertRule` row — see `seed_alert_rules`.
DEFAULTS: dict[str, dict] = {
    "SHORT_DATED_BATCH": {"severity": Severity.WARNING, "threshold": {"days": 90}},
    "BELOW_REORDER_POINT": {"severity": Severity.WARNING, "threshold": {}},
    "ALLOCATION_EXHAUSTED": {"severity": Severity.WARNING, "threshold": {}},
    "REGISTRATION_EXPIRED": {"severity": Severity.CRITICAL, "threshold": {}},
    "REGISTRATION_EXPIRING": {"severity": Severity.WARNING, "threshold": {"days": 60}},
    "BUYER_LICENCE_EXPIRED": {"severity": Severity.CRITICAL, "threshold": {}},
    "CREDIT_LIMIT_EXCEEDED": {"severity": Severity.CRITICAL, "threshold": {}},
    "CREDIT_LIMIT_NEAR": {"severity": Severity.WARNING, "threshold": {"percent": 80}},
    "RECEIVABLE_OVERDUE": {"severity": Severity.WARNING, "threshold": {"days": 30}},
    "SALE_BELOW_COST": {"severity": Severity.WARNING, "threshold": {}},
    "CONTROLLED_QUOTA_NEAR": {"severity": Severity.WARNING, "threshold": {"percent": 80}},
    "CONTROLLED_QUOTA_EXCEEDED": {"severity": Severity.CRITICAL, "threshold": {}},
    "BULK_DISCOUNT_AVAILABLE": {"severity": Severity.INFO, "threshold": {}},
}


def rule_for(*, organization: Organization, code: str, as_of: date | None = None) -> dict:
    """The threshold in force on `as_of`, falling back to the default.

    Reads the unfiltered manager with an explicit organization: checks
    run inside services that already know whose rules they are applying,
    including background work with no request tenant set.
    """
    as_of = as_of or timezone.localdate()
    rule = (
        AlertRule.objects.filter(
            organization=organization,
            code=code,
            is_active=True,
            effective_from__lte=as_of,
        )
        .filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=as_of))
        .order_by("-effective_from")
        .first()
    )
    if rule is not None:
        return {"severity": rule.severity, "threshold": rule.threshold}
    return DEFAULTS.get(code, {"severity": Severity.WARNING, "threshold": {}})


def seed_alert_rules(organization: Organization) -> list[AlertRule]:
    """Materialise the defaults so an organization can edit them.

    A threshold nobody can see is not configuration. Seeding writes the
    defaults as real dated rows on the day the organization starts, so
    changing one produces a new version rather than an unexplained shift.
    """
    created = []
    for code, spec in DEFAULTS.items():
        rule, made = AlertRule.objects.get_or_create(
            organization=organization,
            code=code,
            effective_from=timezone.localdate(),
            defaults={"severity": spec["severity"], "threshold": spec["threshold"]},
        )
        if made:
            created.append(rule)
    return created


# --------------------------------------------------------------------------
# Enforcement
# --------------------------------------------------------------------------


class AlertBlocked(DomainError):
    """A critical alert. The transaction did not happen."""

    default_code = "alert_blocked"
    default_detail = "This action is blocked."


class AcknowledgementRequired(DomainError):
    """A warning the caller has not accepted.

    422 with the codes in `meta`, so a client can present them, collect
    the acknowledgement and retry with `acknowledged` populated.
    """

    default_code = "acknowledgement_required"
    default_detail = "Confirm the warnings before continuing."


#: Alert codes that raise a more specific exception than `AlertBlocked`.
#:
#: The wire contract is the `code`, which is unchanged either way — but
#: Python callers that already catch a named exception should keep
#: working, and a caller that wants to handle *only* an over-limit
#: refusal should not have to inspect a string to do it.
_BLOCKING_EXCEPTIONS: dict[str, type[DomainError]] = {}


def blocks_with(code: str, exception: type[DomainError]) -> None:
    """Register the exception a critical alert raises."""
    _BLOCKING_EXCEPTIONS[code] = exception


def enforce(
    alerts: Iterable[Alert],
    *,
    organization: Organization,
    performed_by: User | None = None,
    acknowledged: Iterable[str] | None = (),
    reason: str = "",
) -> list[Alert]:
    """Apply the severities, recording every acknowledgement.

    Criticals are raised first and all together: telling a user about one
    blocker, then another after they fix it, is three round trips where
    one would have done.

    Returns the informational alerts, which the caller may attach to its
    response.
    """
    alerts = list(alerts)
    accepted = set(acknowledged or ())

    blocking = [alert for alert in alerts if alert.blocking]
    if blocking:
        first = blocking[0]
        raise _BLOCKING_EXCEPTIONS.get(first.code, AlertBlocked)(
            first.title,
            code=first.code.lower(),
            meta={"alerts": [alert.as_dict() for alert in blocking]},
        )

    warnings = [alert for alert in alerts if alert.needs_acknowledgement]
    unacknowledged = [alert for alert in warnings if alert.code not in accepted]
    if unacknowledged:
        raise AcknowledgementRequired(
            unacknowledged[0].title,
            meta={"alerts": [alert.as_dict() for alert in unacknowledged]},
        )

    for alert in warnings:
        record_acknowledgement(
            alert=alert,
            organization=organization,
            performed_by=performed_by,
            reason=reason,
        )

    return [alert for alert in alerts if alert.severity == Severity.INFO]


def record_acknowledgement(
    *,
    alert: Alert,
    organization: Organization,
    performed_by: User | None = None,
    reason: str = "",
) -> AlertAcknowledgement:
    acknowledgement = AlertAcknowledgement.objects.create(
        organization=organization,
        code=alert.code,
        severity=alert.severity,
        subject_type=alert.subject_type,
        subject_id=alert.subject_id or None,
        detail=alert.detail,
        reason=reason,
        acknowledged_by=performed_by,
    )
    audit.record(
        action="core.alert.acknowledged",
        subject=acknowledgement,
        actor=performed_by,
        after={
            "code": alert.code,
            "severity": alert.severity,
            "subject_type": alert.subject_type,
            "subject_id": alert.subject_id,
            "reason": reason,
        },
        organization=organization,
    )
    return acknowledgement


# --------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------

#: A screen shows at most this many before collapsing to a summary.
#: Staff who meet six warnings per sale stop reading warnings, and then
#: the system is worse than one with none — everybody believes the checks
#: are working.
MAX_VISIBLE = 3


def summarise(alerts: list[Alert]) -> dict:
    """What a screen should show, and what it should fold away."""
    ranked = sorted(
        alerts,
        key=lambda alert: [Severity.CRITICAL, Severity.WARNING, Severity.INFO].index(
            alert.severity
        ),
    )
    return {
        "visible": [alert.as_dict() for alert in ranked[:MAX_VISIBLE]],
        "collapsed": max(0, len(ranked) - MAX_VISIBLE),
        "counts": {
            severity: sum(1 for alert in alerts if alert.severity == severity)
            for severity in (Severity.CRITICAL, Severity.WARNING, Severity.INFO)
        },
    }
