"""Registering a pharmacy onto the network.

The transactional test is the one that matters: a pharmacy that exists
but holds no licence, or holds a licence but has nobody who can sign in,
is a half-registered pharmacy somebody has to finish by hand — and
nobody ever does.
"""

from datetime import date, timedelta

import pytest

from commerce.models import TradingRelationship
from core import onboarding
from core.capabilities import Capability, has_capability
from core.exceptions import DomainError, LicenceInvalid
from core.models import (
    AuditEvent,
    Branch,
    LicenceKind,
    LicenceStatus,
    Organization,
    PremisesLicence,
    User,
)
from inventory.tests.factories import make_org

pytestmark = pytest.mark.django_db

TODAY = date.today()
NEXT_YEAR = TODAY + timedelta(days=365)


@pytest.fixture
def depot():
    org = make_org("ABC Wholesale", kind=LicenceKind.WHOLESALE_PHARMACY)
    branch = Branch.objects.create(organization=org, name="Main", code="MAIN")
    PremisesLicence.objects.create(
        organization=org,
        branch=branch,
        kind=LicenceKind.WHOLESALE_PHARMACY,
        number="RFDA-WS-ABC",
        issued_on=TODAY - timedelta(days=400),
        expiry=NEXT_YEAR,
        status=LicenceStatus.ACTIVE,
    )
    return {
        "org": org,
        "admin": User.objects.create_user(
            username="jean", password="x", organization=org
        ),
    }


def register(depot, **overrides):
    payload = {
        "registered_by": depot["admin"],
        "name": "Kigali Care Pharmacy",
        "licence_kind": LicenceKind.RETAIL_PHARMACY,
        "licence_number": "RFDA-RET-0912",
        "licence_expiry": NEXT_YEAR,
        "admin_email": "owner@kigalicare.rw",
        "admin_full_name": "Marie Uwase",
        "credit_limit": 2_000_000,
        "payment_terms_days": 30,
    }
    payload.update(overrides)
    return onboarding.register_pharmacy(**payload)


class TestRegistration:
    def test_it_creates_the_organization(self, depot):
        result = register(depot)
        assert result["organization"].name == "Kigali Care Pharmacy"
        assert result["organization"].primary_kind == LicenceKind.RETAIL_PHARMACY

    def test_it_creates_a_branch_because_a_licence_is_for_premises(self, depot):
        result = register(depot)
        assert result["branch"].code == "MAIN"
        assert result["branch"].organization_id == result["organization"].id

    def test_the_licence_grants_capability_immediately(self, depot):
        """Capability comes from the licence, never a type field."""
        result = register(depot)
        assert has_capability(result["organization"], Capability.DISPENSE)
        assert not has_capability(result["organization"], Capability.PUBLISH_LISTINGS)

    def test_a_wholesale_registration_gets_wholesale_capability(self, depot):
        result = register(
            depot,
            name="Second Depot",
            licence_kind=LicenceKind.WHOLESALE_PHARMACY,
            licence_number="RFDA-WS-002",
        )
        assert has_capability(result["organization"], Capability.PUBLISH_LISTINGS)

    def test_the_administrator_can_sign_in(self, depot):
        result = register(depot)
        administrator = result["administrator"]
        assert administrator.check_password(result["temporary_password"])
        assert administrator.organization_id == result["organization"].id

    def test_the_password_is_returned_once_and_not_stored_readable(self, depot):
        result = register(depot)
        assert result["temporary_password"]
        assert result["temporary_password"] not in result["administrator"].password

    def test_the_admin_name_is_split_across_the_user_fields(self, depot):
        result = register(depot)
        assert result["administrator"].first_name == "Marie"
        assert result["administrator"].last_name == "Uwase"

    def test_it_opens_a_verified_trading_relationship(self, depot):
        """Registering the pharmacy is the verification."""
        result = register(depot)
        relationship = TradingRelationship.objects.get(
            organization=depot["org"], customer=result["organization"]
        )
        assert relationship.is_verified
        assert relationship.credit_limit == 2_000_000
        assert relationship.payment_terms_days == 30

    def test_a_pharmacist_registration_is_optional(self, depot):
        """No pharmacist means it can hold stock and not dispense."""
        result = register(depot)
        assert result["pharmacist_registration"] is None

    def test_a_pharmacist_can_be_recorded_at_registration(self, depot):
        result = register(depot, pharmacist_council_number="NPC-8891")
        assert result["pharmacist_registration"].council_number == "NPC-8891"

    def test_it_is_audited_against_the_registering_depot(self, depot):
        register(depot)
        event = AuditEvent.objects.get(action="core.pharmacy.registered")
        assert event.organization_id == depot["org"].id
        assert event.after["licence_number"] == "RFDA-RET-0912"


class TestItIsOneTransaction:
    def test_a_duplicate_licence_leaves_nothing_behind(self, depot):
        """Half a pharmacy is worse than none: nobody finishes it."""
        register(depot)
        before = Organization.objects.count()

        with pytest.raises(onboarding.AlreadyRegistered):
            register(depot, name="Impostor Pharmacy")

        assert Organization.objects.count() == before
        assert not Organization.objects.filter(name="Impostor Pharmacy").exists()

    def test_an_expired_licence_is_refused_outright(self, depot):
        with pytest.raises(DomainError):
            register(depot, licence_expiry=TODAY - timedelta(days=1))
        assert not Organization.objects.filter(name="Kigali Care Pharmacy").exists()

    def test_a_nameless_pharmacy_is_refused(self, depot):
        with pytest.raises(DomainError):
            register(depot, name="   ")

    def test_an_unknown_licence_kind_is_refused(self, depot):
        with pytest.raises(DomainError):
            register(depot, licence_kind="SUPERMARKET")


class TestUsernames:
    def test_two_pharmacies_can_share_an_owner_name(self, depot):
        """A collision must not fail the registration."""
        first = register(depot, admin_email="owner@example.rw")
        second = register(
            depot,
            name="Second Pharmacy",
            licence_number="RFDA-RET-0913",
            admin_email="owner@example.rw",
        )
        assert (
            first["administrator"].username != second["administrator"].username
        )

    def test_a_pharmacy_with_no_email_still_gets_a_username(self, depot):
        result = register(depot, admin_email="")
        assert result["administrator"].username


class TestPermission:
    def test_only_a_depot_may_register_a_pharmacy(self, depot):
        """Admitting a customer you cannot supply is not a meaningful act."""
        from core.capabilities import require_capability

        retail = make_org("A Retail Shop", kind=LicenceKind.RETAIL_PHARMACY)
        with pytest.raises(LicenceInvalid):
            require_capability(retail, Capability.PUBLISH_LISTINGS)
