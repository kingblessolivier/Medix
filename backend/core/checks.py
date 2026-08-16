"""Compliance alerts: licences and registrations running out.

A premises licence that lapses removes every capability the organization
had — silently, on a date nobody was watching. A pharmacist registration
that lapses stops dispensing. Both are the kind of thing that is obvious
in hindsight and invisible in advance, which is what an alert is for.

Neither of these adds a refusal. Expiry is *already* enforced:
`has_capability` stops honouring a lapsed licence and the counter stops
accepting a lapsed registration. These alerts exist to explain a system
that has quietly stopped working, and to give warning before it does.

Thresholds are effective-dated like every other one — see
`core.alerts.rule_for`.
"""

from __future__ import annotations

from datetime import date

from django.utils import timezone

from core.alerts import Alert, Severity, about, rule_for
from core.models import (
    LicenceStatus,
    Organization,
    PharmacistRegistration,
    PremisesLicence,
)


def licences(*, organization: Organization, as_of: date | None = None) -> list[Alert]:
    """Premises licences expired, suspended, or close to expiring."""
    as_of = as_of or timezone.localdate()
    rule = rule_for(organization=organization, code="LICENCE_EXPIRING", as_of=as_of)
    horizon = rule["threshold"].get("days", 60)

    found = []
    for licence in PremisesLicence.objects.filter(organization=organization):
        if licence.status != LicenceStatus.ACTIVE:
            found.append(
                about(
                    licence,
                    code="LICENCE_INVALID",
                    severity=Severity.CRITICAL,
                    title=(
                        f"{licence.get_kind_display()} licence is "
                        f"{licence.get_status_display().lower()}"
                    ),
                    detail=f"Licence {licence.number}.",
                    meta={"number": licence.number, "status": licence.status, "days": -1},
                )
            )
            continue

        days = (licence.expiry - as_of).days
        if days < 0:
            found.append(
                about(
                    licence,
                    code="LICENCE_EXPIRED",
                    severity=Severity.CRITICAL,
                    title=f"{licence.get_kind_display()} licence expired",
                    detail=(
                        f"Licence {licence.number} lapsed {licence.expiry:%d %b %Y}. "
                        "Capability is withdrawn."
                    ),
                    meta={
                        "number": licence.number,
                        "expiry": licence.expiry.isoformat(),
                        "days": days,
                    },
                )
            )
        elif days <= horizon:
            found.append(
                about(
                    licence,
                    code="LICENCE_EXPIRING",
                    severity=rule["severity"],
                    title=f"{licence.get_kind_display()} licence expires in {days} days",
                    detail=f"Licence {licence.number}.",
                    meta={"number": licence.number, "days": days},
                )
            )
    return sorted(found, key=lambda alert: alert.meta.get("days", 0))


def registrations(
    *, organization: Organization, as_of: date | None = None
) -> list[Alert]:
    """Pharmacist registrations expired or close to it.

    A pharmacy whose only registered pharmacist has lapsed can hold stock
    and cannot dispense. That is a real state rather than a broken one,
    so this reports it; the refusal happens where it belongs, at the
    counter.
    """
    as_of = as_of or timezone.localdate()
    rule = rule_for(
        organization=organization, code="PHARMACIST_REGISTRATION_EXPIRING", as_of=as_of
    )
    horizon = rule["threshold"].get("days", 60)

    found = []
    for registration in PharmacistRegistration.objects.filter(
        organization=organization
    ).select_related("user"):
        days = (registration.expiry - as_of).days
        if registration.status != LicenceStatus.ACTIVE or days < 0:
            found.append(
                about(
                    registration,
                    code="PHARMACIST_REGISTRATION_EXPIRED",
                    severity=Severity.CRITICAL,
                    title=f"{registration.user} cannot dispense",
                    detail=(
                        f"Registration {registration.council_number} expired "
                        f"{registration.expiry:%d %b %Y}."
                    ),
                    meta={"council_number": registration.council_number, "days": days},
                )
            )
        elif days <= horizon:
            found.append(
                about(
                    registration,
                    code="PHARMACIST_REGISTRATION_EXPIRING",
                    severity=rule["severity"],
                    title=(
                        f"{registration.user}'s registration expires in {days} days"
                    ),
                    detail=f"Registration {registration.council_number}.",
                    meta={"council_number": registration.council_number, "days": days},
                )
            )
    return sorted(found, key=lambda alert: alert.meta.get("days", 0))


def compliance_state(*, organization: Organization, as_of: date | None = None) -> dict:
    """Everything a compliance officer checks, in one call.

    Assembled from live records rather than from a status column somebody
    has to remember to update. A compliance dashboard that can be stale is
    worse than none, because it is believed.
    """
    as_of = as_of or timezone.localdate()
    held = list(PremisesLicence.objects.filter(organization=organization))
    people = list(
        PharmacistRegistration.objects.filter(organization=organization).select_related(
            "user"
        )
    )

    return {
        "as_of": as_of.isoformat(),
        "licences": [
            {
                "id": str(licence.id),
                "kind": licence.kind,
                "kind_label": licence.get_kind_display(),
                "number": licence.number,
                "expiry": licence.expiry.isoformat(),
                "days_remaining": (licence.expiry - as_of).days,
                "status": licence.status,
                "is_valid": licence.is_valid,
            }
            for licence in sorted(held, key=lambda row: row.expiry)
        ],
        "registrations": [
            {
                "id": str(registration.id),
                "name": str(registration.user),
                "council_number": registration.council_number,
                "expiry": registration.expiry.isoformat(),
                "days_remaining": (registration.expiry - as_of).days,
                "status": registration.status,
                "is_valid": registration.is_valid,
            }
            for registration in sorted(people, key=lambda row: row.expiry)
        ],
        "alerts": [
            alert.as_dict()
            for alert in [
                *licences(organization=organization, as_of=as_of),
                *registrations(organization=organization, as_of=as_of),
            ]
        ],
    }
