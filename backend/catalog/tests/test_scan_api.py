"""Scanning a pack resolves it to a product and batch."""

import pytest
from rest_framework.test import APIClient

from catalog.gs1 import FNC1
from core.models import User
from inventory.tests.factories import make_batch, make_org, make_product

pytestmark = pytest.mark.django_db

GTIN = "05012345678900"


@pytest.fixture
def pharmacy():
    org = make_org()
    user = User.objects.create_user(username="marie", password="x", organization=org)
    product = make_product(org)
    product.gtin = GTIN
    product.save(update_fields=["gtin"])
    batch = make_batch(org, product, number="AMX-0021")
    client = APIClient()
    client.force_authenticate(user=user)
    return {"org": org, "client": client, "product": product, "batch": batch}


def scan(client, code: str):
    return client.post("/api/v1/scan/", {"code": code}, format="json")


class TestResolution:
    def test_full_pack_resolves_product_and_batch(self, pharmacy):
        code = f"01{GTIN}17270408" + "10AMX-0021"
        response = scan(pharmacy["client"], code)

        assert response.status_code == 200
        assert response.data["matched"] is True
        assert response.data["product"]["name"] == "Amoxicillin 500mg"
        assert response.data["batch"]["batch_number"] == "AMX-0021"
        assert str(response.data["expiry_date"]) == "2027-04-08"

    def test_gtin_alone_resolves_the_product(self, pharmacy):
        response = scan(pharmacy["client"], f"01{GTIN}")
        assert response.data["matched"] is True
        assert response.data["batch"] is None

    def test_batch_number_alone_identifies_the_product(self, pharmacy):
        """A batch found by number tells us its product without a GTIN."""
        response = scan(pharmacy["client"], f"10AMX-0021{FNC1}17270408")
        assert response.data["matched"] is True
        assert response.data["product"]["name"] == "Amoxicillin 500mg"

    def test_expiry_returned_for_prefilling_a_receipt(self, pharmacy):
        """The point of the feature: batch and expiry without typing."""
        response = scan(pharmacy["client"], f"10AMX-0034{FNC1}17280229")
        assert response.data["batch_number"] == "AMX-0034"
        assert str(response.data["expiry_date"]) == "2028-02-29"


class TestUnmatched:
    def test_unknown_gtin_reports_unmatched_rather_than_guessing(self, pharmacy):
        response = scan(pharmacy["client"], "0105000000000050")
        assert response.status_code == 200
        assert response.data["matched"] is False
        assert response.data["product"] is None
        assert response.data["gtin"] == "05000000000050"

    def test_unknown_batch_still_returns_what_the_barcode_carried(self, pharmacy):
        response = scan(pharmacy["client"], f"10ZZZ-9999{FNC1}17270408")
        assert response.data["matched"] is False
        assert response.data["batch_number"] == "ZZZ-9999"
        assert str(response.data["expiry_date"]) == "2027-04-08"


class TestRejections:
    def test_malformed_barcode_is_422_with_the_reason(self, pharmacy):
        response = scan(pharmacy["client"], "99123456")
        assert response.status_code == 422
        assert response.data["error"]["code"] == "invalid_barcode"

    def test_bad_check_digit_rejected(self, pharmacy):
        response = scan(pharmacy["client"], "0105012345678901")
        assert response.status_code == 422
        assert "check digit" in response.data["error"]["message"]

    def test_plain_barcode_rejected(self, pharmacy):
        assert scan(pharmacy["client"], "5012345678900").status_code == 422

    def test_unauthenticated_rejected(self):
        assert APIClient().post("/api/v1/scan/", {"code": "x"}).status_code == 401


class TestCrossTenant:
    def test_a_scan_never_reaches_another_pharmacy_stock(self, pharmacy):
        other = make_org("ABC Wholesale", kind="WHOLESALE")
        other_user = User.objects.create_user(username="jean", password="x", organization=other)
        client = APIClient()
        client.force_authenticate(user=other_user)

        response = scan(client, f"01{GTIN}17270408" + "10AMX-0021")
        assert response.status_code == 200
        assert response.data["matched"] is False
        assert response.data["batch"] is None
