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


class TestComplianceCanBeChanged:
    """A dashboard that reports a lapse and cannot fix it is ignored.

    Both of these were read-only, which meant a pharmacy could see
    "dispensing: blocked" and had nowhere in the product to record the
    registration that unblocks it.
    """

    @pytest.fixture
    def pharmacy(self, db):
        from rest_framework.test import APIClient

        from core.models import Branch, Organization, User

        org = Organization.objects.create(name="Kigali Care", primary_kind="RETAIL")
        branch = Branch.objects.create(organization=org, name="Main", code="MAIN")
        user = User.objects.create_user(
            username="marie", password="x", organization=org, first_name="Marie"
        )
        client = APIClient()
        client.force_authenticate(user=user)
        return {"org": org, "branch": branch, "user": user, "client": client}

    def test_a_licence_can_be_recorded(self, pharmacy):
        from core.models import LicenceKind

        response = pharmacy["client"].post(
            "/api/v1/licences/",
            {
                "branch": str(pharmacy["branch"].id),
                "kind": LicenceKind.RETAIL_PHARMACY,
                "number": "RFDA-0001",
                "issued_on": str(date.today()),
                "expiry": str(date.today() + timedelta(days=365)),
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.data["is_valid"]
        assert response.data["days_to_expiry"] == 365

    def test_renewing_adds_a_record_rather_than_editing_one(self, pharmacy):
        """A licence is evidence of what was permitted between two dates."""
        from core.models import LicenceKind, PremisesLicence

        for number, offset in [("RFDA-0001", -30), ("RFDA-0002", 365)]:
            pharmacy["client"].post(
                "/api/v1/licences/",
                {
                    "branch": str(pharmacy["branch"].id),
                    "kind": LicenceKind.RETAIL_PHARMACY,
                    "number": number,
                    "issued_on": str(date.today() - timedelta(days=400)),
                    "expiry": str(date.today() + timedelta(days=offset)),
                },
                format="json",
            )
        assert PremisesLicence.objects.filter(organization=pharmacy["org"]).count() == 2

    def test_a_registration_can_be_recorded(self, pharmacy):
        response = pharmacy["client"].post(
            "/api/v1/pharmacist-registrations/",
            {
                "user": str(pharmacy["user"].id),
                "council_number": "RPC-0114",
                "issued_on": str(date.today() - timedelta(days=30)),
                "expiry": str(date.today() + timedelta(days=335)),
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.data["is_valid"]
        assert response.data["user_name"] == "Marie"

    def test_it_unblocks_verifying_a_prescription(self, pharmacy):
        """The whole point. Without one, nothing can be dispensed at all."""
        from sales.models import Patient, Prescription

        pharmacy["client"].post(
            "/api/v1/pharmacist-registrations/",
            {
                "user": str(pharmacy["user"].id),
                "council_number": "RPC-0114",
                "issued_on": str(date.today() - timedelta(days=30)),
                "expiry": str(date.today() + timedelta(days=335)),
            },
            format="json",
        )
        patient = Patient.objects.create(
            organization=pharmacy["org"], full_name="Aline M."
        )
        script = Prescription.objects.create(
            organization=pharmacy["org"], patient=patient
        )

        response = pharmacy["client"].post(
            f"/api/v1/prescriptions/{script.id}/verify/", {}, format="json"
        )
        assert response.status_code == 200
        assert response.data["status"] == "VERIFIED"

    def test_another_tenants_licences_are_invisible(self, pharmacy):
        from core.models import Branch, LicenceKind, Organization, PremisesLicence

        other = Organization.objects.create(name="ABC", primary_kind="WHOLESALE")
        other_branch = Branch.objects.create(organization=other, name="Depot", code="DEP")
        PremisesLicence.objects.create(
            organization=other,
            branch=other_branch,
            kind=LicenceKind.WHOLESALE_PHARMACY,
            number="RFDA-OTHER",
            issued_on=date.today(),
            expiry=date.today() + timedelta(days=365),
        )
        response = pharmacy["client"].get("/api/v1/licences/")
        assert [row["number"] for row in response.data["results"]] == []

    def test_colleagues_are_scoped_to_this_pharmacy(self, pharmacy):
        from core.models import Organization, User

        other = Organization.objects.create(name="ABC", primary_kind="WHOLESALE")
        User.objects.create_user(username="jean", password="x", organization=other)

        response = pharmacy["client"].get("/api/v1/colleagues/")
        assert [row["username"] for row in response.data["results"]] == ["marie"]
