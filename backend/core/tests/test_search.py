"""Global search.

Two properties matter more than the matching: it never crosses the tenant
boundary, and patient records are gated on dispensing capability rather
than folded into the general sweep.
"""

from datetime import date, timedelta

import pytest

from core import search
from core.models import Branch, LicenceKind, LicenceStatus, PremisesLicence, User
from inventory.tests.factories import make_batch, make_org, make_product
from sales.models import Patient

pytestmark = pytest.mark.django_db

TODAY = date.today()


def licence(org, kind):
    branch, _ = Branch.objects.get_or_create(
        organization=org, code="MAIN", defaults={"name": "Main"}
    )
    return PremisesLicence.objects.create(
        organization=org,
        branch=branch,
        kind=kind,
        number=f"RFDA-{kind}-{org.name[:4]}",
        issued_on=TODAY - timedelta(days=400),
        expiry=TODAY + timedelta(days=365),
        status=LicenceStatus.ACTIVE,
    )


@pytest.fixture
def pharmacy():
    org = make_org("Kigali Care", kind=LicenceKind.RETAIL_PHARMACY)
    licence(org, LicenceKind.RETAIL_PHARMACY)
    user = User.objects.create_user(username="marie", password="x", organization=org)
    product = make_product(org, "Amoxicillin 500mg")
    make_batch(org, product, number="AMX-0021")
    Patient.objects.create(organization=org, full_name="Aline Mukamana", phone="0788111222")
    return {"org": org, "user": user, "product": product}


class TestMatching:
    def test_a_product_is_found_by_name(self, pharmacy):
        found = search.search(user=pharmacy["user"], term="amoxi")
        assert any(hit["kind"] == "product" for hit in found["results"])

    def test_a_batch_is_found_by_its_number(self, pharmacy):
        """The number on the carton, typed straight in."""
        found = search.search(user=pharmacy["user"], term="AMX-0021")
        batches = [hit for hit in found["results"] if hit["kind"] == "batch"]
        assert batches and batches[0]["title"] == "AMX-0021"

    def test_every_result_carries_the_screen_that_opens_it(self, pharmacy):
        found = search.search(user=pharmacy["user"], term="amoxi")
        assert all(hit["screen"] for hit in found["results"])

    def test_one_character_returns_nothing(self, pharmacy):
        """One letter matches most of a catalogue; that is noise."""
        assert search.search(user=pharmacy["user"], term="a")["results"] == []

    def test_a_blank_term_returns_nothing(self, pharmacy):
        assert search.search(user=pharmacy["user"], term="   ")["results"] == []


class TestTenancy:
    def test_it_never_crosses_the_organization_boundary(self, pharmacy):
        other = make_org("Someone Else", kind=LicenceKind.RETAIL_PHARMACY)
        make_product(other, "Amoxicillin 500mg")

        found = search.search(user=pharmacy["user"], term="amoxi")
        products = [hit for hit in found["results"] if hit["kind"] == "product"]
        assert len(products) == 1

    def test_a_user_with_no_organization_finds_nothing(self):
        stranger = User.objects.create_user(username="nobody", password="x")
        assert search.search(user=stranger, term="amoxi")["results"] == []


class TestPatientGating:
    def test_a_dispensing_pharmacy_finds_patients(self, pharmacy):
        found = search.search(user=pharmacy["user"], term="Aline")
        assert any(hit["kind"] == "patient" for hit in found["results"])

    def test_a_depot_does_not(self, pharmacy):
        """A search returning patients to anyone who can search leaks."""
        depot = make_org("ABC Wholesale", kind=LicenceKind.WHOLESALE_PHARMACY)
        licence(depot, LicenceKind.WHOLESALE_PHARMACY)
        buyer = User.objects.create_user(
            username="jean", password="x", organization=depot
        )
        Patient.objects.create(organization=depot, full_name="Aline Mukamana")

        found = search.search(user=buyer, term="Aline")
        assert not any(hit["kind"] == "patient" for hit in found["results"])

    def test_a_patient_is_found_by_phone(self, pharmacy):
        found = search.search(user=pharmacy["user"], term="0788111")
        assert any(hit["kind"] == "patient" for hit in found["results"])
