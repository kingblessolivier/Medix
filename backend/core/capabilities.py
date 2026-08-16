"""Capability derives from held licences, never a type field.

An organization may hold retail, wholesale and importer licences at once —
a wholesale pharmacy with a retail counter is common. So "what may this
organization do" is answered by asking which valid licences it holds, not
by reading a label. See ADR-006.

The consequence a regulator expects: **licence expiry revokes capability
automatically.** A branch whose retail licence lapsed cannot open a till,
without anyone having to remember to switch something off.

See docs/06-compliance.md §1 and docs/16-security.md.
"""

from __future__ import annotations

from datetime import date

from django.db.models import Q
from django.utils import timezone

from core.exceptions import LicenceInvalid
from core.models import LicenceKind, LicenceStatus, Organization, PremisesLicence


class Capability:
    """What an organization or branch may do."""

    SELL_RETAIL = "SELL_RETAIL"
    DISPENSE = "DISPENSE"
    SELL_WHOLESALE = "SELL_WHOLESALE"
    PUBLISH_LISTINGS = "PUBLISH_LISTINGS"
    IMPORT = "IMPORT"
    DISTRIBUTE = "DISTRIBUTE"


#: Which licence kinds grant which capability.
GRANTS: dict[str, set[str]] = {
    LicenceKind.RETAIL_PHARMACY: {Capability.SELL_RETAIL, Capability.DISPENSE},
    LicenceKind.WHOLESALE_PHARMACY: {
        Capability.SELL_WHOLESALE,
        Capability.PUBLISH_LISTINGS,
        Capability.DISTRIBUTE,
    },
    LicenceKind.IMPORTER: {Capability.IMPORT, Capability.PUBLISH_LISTINGS},
    LicenceKind.DISTRIBUTOR: {Capability.DISTRIBUTE},
}


def valid_licences(organization: Organization, *, branch=None, as_of: date | None = None):
    as_of = as_of or timezone.localdate()
    qs = PremisesLicence.objects.filter(
        organization=organization, status=LicenceStatus.ACTIVE, expiry__gte=as_of
    )
    if branch is not None:
        qs = qs.filter(branch=branch)
    return qs


def capabilities_of(
    organization: Organization, *, branch=None, as_of: date | None = None
) -> set[str]:
    """Every capability the held, valid licences grant."""
    granted: set[str] = set()
    for kind in valid_licences(organization, branch=branch, as_of=as_of).values_list(
        "kind", flat=True
    ):
        granted |= GRANTS.get(kind, set())
    return granted


def has_capability(
    organization: Organization, capability: str, *, branch=None, as_of: date | None = None
) -> bool:
    return capability in capabilities_of(organization, branch=branch, as_of=as_of)


def require_capability(
    organization: Organization, capability: str, *, branch=None, as_of: date | None = None
) -> None:
    """Refuse, naming the licence that is missing or lapsed.

    The message says what to fix rather than merely that it is forbidden.
    """
    if has_capability(organization, capability, branch=branch, as_of=as_of):
        return

    needed = [kind for kind, grants in GRANTS.items() if capability in grants]
    lapsed = PremisesLicence.objects.filter(
        Q(organization=organization),
        Q(kind__in=needed),
        Q(status=LicenceStatus.ACTIVE) & Q(expiry__lt=(as_of or timezone.localdate()))
        | ~Q(status=LicenceStatus.ACTIVE),
    ).first()

    if lapsed is not None:
        raise LicenceInvalid(
            f"{lapsed.get_kind_display()} licence {lapsed.number} is not valid.",
            meta={"licence_id": str(lapsed.id), "capability": capability},
        )

    raise LicenceInvalid(
        f"No valid {LicenceKind(needed[0]).label.lower()} licence.",
        meta={"capability": capability, "requires": needed},
    )
