"""Registration alerts.

A product whose Rwanda FDA registration has lapsed cannot be listed or
shipped. `RegistrationInvalid` existed but nothing checked it on publish
or dispatch — this is that check, at the two points where it matters.
"""

from __future__ import annotations

from datetime import date

from django.utils import timezone

from catalog.models import RegistrationStatus
from core.alerts import Alert, Severity, about, rule_for


def registration(*, product, as_of: date | None = None) -> list[Alert]:
    """Expired blocks; expiring warns.

    A product with no registration row is not flagged. Consumables,
    cosmetics and devices have none, and warning about every box of
    plasters would train people to ignore the warning that matters.
    """
    as_of = as_of or timezone.localdate()
    record = getattr(product, "registration", None)
    if record is None:
        return []

    if record.status != RegistrationStatus.REGISTERED:
        return [
            about(
                product,
                code="REGISTRATION_EXPIRED",
                severity=Severity.CRITICAL,
                title=f"{product.name} registration is {record.get_status_display().lower()}",
                detail=f"Registration {record.registration_number}.",
                meta={"status": record.status},
            )
        ]

    if record.registration_expiry is None:
        return []

    if record.registration_expiry < as_of:
        return [
            about(
                product,
                code="REGISTRATION_EXPIRED",
                severity=Severity.CRITICAL,
                title=f"{product.name} registration expired",
                detail=(
                    f"Registration {record.registration_number} lapsed "
                    f"{record.registration_expiry:%d %b %Y}."
                ),
                meta={"expiry": record.registration_expiry.isoformat()},
            )
        ]

    rule = rule_for(
        organization=product.organization, code="REGISTRATION_EXPIRING", as_of=as_of
    )
    days = (record.registration_expiry - as_of).days
    if days <= rule["threshold"].get("days", 60):
        return [
            about(
                product,
                code="REGISTRATION_EXPIRING",
                severity=rule["severity"],
                title=f"{product.name} registration expires in {days} days",
                detail=f"Registration {record.registration_number}.",
                meta={"days": days},
            )
        ]
    return []
