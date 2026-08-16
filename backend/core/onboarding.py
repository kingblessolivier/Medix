"""Registering a pharmacy onto the platform.

This is a **closed distribution network**: a depot admits specific retail
pharmacies, and those pharmacies then see the depot's published
catalogue. Nobody signs themselves up. So onboarding is an act performed
*by* one organization *on* another, and it is one transaction — a
pharmacy that exists but holds no licence, or holds a licence but has
nobody who can log in, is a half-registered pharmacy somebody has to
finish by hand.

What it creates, together or not at all:

1. the `Organization`
2. a main `Branch`, because a licence is granted to premises
3. the `PremisesLicence` that gives it capability at all
4. an administrator who can sign in
5. the `TradingRelationship` that lets it order from the depot that
   registered it

Capability comes from the licence, never from a type field — so a
pharmacy registered without a valid licence can sign in and can do
nothing, which is the correct outcome rather than a bug.

See docs/01-overview.md and ADR-006.
"""

from __future__ import annotations

import secrets
from datetime import date

from django.db import transaction
from django.utils import timezone

from commerce.models import TradingRelationship
from core import audit
from core.exceptions import DomainError
from core.models import (
    Branch,
    LicenceKind,
    LicenceStatus,
    Organization,
    PharmacistRegistration,
    PremisesLicence,
    User,
)


class AlreadyRegistered(DomainError):
    default_code = "already_registered"
    default_detail = "A pharmacy with that licence number is already registered."


def _unique_username(base: str) -> str:
    """A sign-in name nobody else holds.

    Derived from the email or the pharmacy name, then suffixed. Handing
    the admin a name they did not choose is better than failing
    registration because a common name was taken.
    """
    candidate = base.strip().lower().replace(" ", ".")[:140] or "admin"
    if not User.objects.filter(username=candidate).exists():
        return candidate
    for _ in range(20):
        suffix = secrets.token_hex(2)
        attempt = f"{candidate}.{suffix}"
        if not User.objects.filter(username=attempt).exists():
            return attempt
    raise DomainError("Could not allocate a username.", code="username_unavailable")


@transaction.atomic
def register_pharmacy(
    *,
    registered_by: User,
    name: str,
    licence_kind: str,
    licence_number: str,
    licence_expiry: date,
    licence_issued_on: date | None = None,
    tin: str = "",
    branch_name: str = "Main",
    branch_code: str = "MAIN",
    address: str = "",
    admin_full_name: str = "",
    admin_email: str = "",
    admin_phone: str = "",
    pharmacist_council_number: str = "",
    pharmacist_expiry: date | None = None,
    credit_limit: int = 0,
    payment_terms_days: int = 0,
    supplier: Organization | None = None,
) -> dict:
    """Create a pharmacy, its licence, its administrator and its account.

    `supplier` is the depot admitting it, defaulting to the registering
    user's own organization. The trading relationship is created
    **verified**, because the act of registering a pharmacy is the
    verification — the depot has just entered its licence number.

    Returns the temporary password rather than emailing it, so the depot
    hands it over deliberately. It is shown once and never stored in
    readable form.
    """
    supplier = supplier or registered_by.organization
    if supplier is None:
        raise DomainError(
            "Only an organization can register a pharmacy.", code="no_organization"
        )
    if licence_kind not in LicenceKind.values:
        raise DomainError(f"Unknown licence kind '{licence_kind}'.", code="unknown_licence_kind")
    if not name.strip():
        raise DomainError("A pharmacy needs a name.", code="name_required")
    if not licence_number.strip():
        raise DomainError("A pharmacy needs a licence number.", code="licence_required")
    if licence_expiry <= timezone.localdate():
        raise DomainError(
            "That licence has already expired.",
            code="licence_expired",
            meta={"expiry": licence_expiry.isoformat()},
        )
    if PremisesLicence.objects.filter(number=licence_number.strip()).exists():
        raise AlreadyRegistered(
            f"Licence {licence_number.strip()} is already on the platform.",
            meta={"licence_number": licence_number.strip()},
        )

    organization = Organization.objects.create(
        name=name.strip(),
        tin=tin.strip(),
        primary_kind=licence_kind,
        created_by=registered_by,
    )
    branch = Branch.objects.create(
        organization=organization,
        name=branch_name.strip() or "Main",
        code=(branch_code.strip() or "MAIN").upper(),
        address=address,
        created_by=registered_by,
    )
    licence = PremisesLicence.objects.create(
        organization=organization,
        branch=branch,
        kind=licence_kind,
        number=licence_number.strip(),
        issued_on=licence_issued_on or timezone.localdate(),
        expiry=licence_expiry,
        status=LicenceStatus.ACTIVE,
        created_by=registered_by,
    )

    password = secrets.token_urlsafe(9)
    administrator = User.objects.create_user(
        username=_unique_username(admin_email or name),
        email=admin_email.strip(),
        password=password,
        organization=organization,
        phone=admin_phone.strip(),
    )
    if admin_full_name.strip():
        parts = admin_full_name.strip().split(" ", 1)
        administrator.first_name = parts[0]
        administrator.last_name = parts[1] if len(parts) > 1 else ""
        administrator.save(update_fields=["first_name", "last_name"])

    # A pharmacy without a registered pharmacist can hold stock and
    # cannot dispense. Recording one now is optional precisely because
    # that gap is a real state, not a setup error to paper over.
    registration = None
    if pharmacist_council_number.strip():
        registration = PharmacistRegistration.objects.create(
            organization=organization,
            user=administrator,
            council_number=pharmacist_council_number.strip(),
            issued_on=timezone.localdate(),
            expiry=pharmacist_expiry or licence_expiry,
            created_by=registered_by,
        )

    relationship = None
    if organization.id != supplier.id:
        relationship = TradingRelationship.objects.create(
            organization=supplier,
            customer=organization,
            credit_limit=credit_limit,
            payment_terms_days=payment_terms_days,
            is_verified=True,
            verified_at=timezone.now(),
            created_by=registered_by,
        )

    audit.record(
        action="core.pharmacy.registered",
        subject=organization,
        actor=registered_by,
        after={
            "name": organization.name,
            "licence_kind": licence_kind,
            "licence_number": licence.number,
            "licence_expiry": licence.expiry,
            "registered_by_organization": supplier.name,
            "administrator": administrator.username,
            "credit_limit": credit_limit,
            "payment_terms_days": payment_terms_days,
        },
        organization=supplier,
    )

    return {
        "organization": organization,
        "branch": branch,
        "licence": licence,
        "administrator": administrator,
        # Shown once. Never stored readable, never emailed from here —
        # the depot hands it over deliberately.
        "temporary_password": password,
        "pharmacist_registration": registration,
        "relationship": relationship,
    }
