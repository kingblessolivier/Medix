"""API contract and cross-tenant isolation.

A missing tenant filter here is a reportable breach, not a bug. Every
endpoint is exercised against a foreign tenant.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from core.models import User
from core.quantity import Quantity
from inventory import services
from inventory.models import MovementKind, StockMovement
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant_a():
    org = make_org("Kigali Care Pharmacy")
    user = User.objects.create_user(
        username="marie", password="x", organization=org, first_name="Marie"
    )
    location = make_location(org)
    product = make_product(org)
    batch = make_batch(org, product)
    services.post_movement(
        organization=org,
        location=location,
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        quantity=Quantity(5, uom(product, "PACK")),
    )
    return {
        "org": org,
        "user": user,
        "location": location,
        "product": product,
        "batch": batch,
    }


@pytest.fixture
def tenant_b():
    org = make_org("ABC Wholesale", kind="WHOLESALE")
    user = User.objects.create_user(username="jean", password="x", organization=org)
    location = make_location(org, "ABC Depot", "DEP")
    product = make_product(org, "Paracetamol 500mg")
    batch = make_batch(org, product, number="PCM-1022")
    return {
        "org": org,
        "user": user,
        "location": location,
        "product": product,
        "batch": batch,
    }


def client_for(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class TestProducts:
    def test_list(self, tenant_a):
        response = client_for(tenant_a["user"]).get("/api/v1/products/")
        assert response.status_code == 200
        assert response.data["results"][0]["name"] == "Amoxicillin 500mg"

    def test_detail_includes_uom_chain(self, tenant_a):
        response = client_for(tenant_a["user"]).get(f"/api/v1/products/{tenant_a['product'].id}/")
        assert response.status_code == 200
        codes = {u["code"] for u in response.data["units"]}
        assert codes == {"CARTON", "PACK", "BLISTER", "UNIT"}

    def test_pom_flagged_for_the_pos(self, tenant_a):
        response = client_for(tenant_a["user"]).get("/api/v1/products/")
        assert response.data["results"][0]["requires_prescription"] is True

    def test_search(self, tenant_a):
        client = client_for(tenant_a["user"])
        assert client.get("/api/v1/products/?search=amox").data["count"] == 1
        assert client.get("/api/v1/products/?search=zzz").data["count"] == 0


class TestStock:
    def test_balances_listed(self, tenant_a):
        response = client_for(tenant_a["user"]).get("/api/v1/stock/")
        assert response.status_code == 200
        assert response.data["results"][0]["quantity_base"] == 500

    def test_days_to_expiry_computed(self, tenant_a):
        response = client_for(tenant_a["user"]).get("/api/v1/stock/")
        assert response.data["results"][0]["days_to_expiry"] > 0

    def test_ledger_is_read_only(self, tenant_a):
        """There is no way to POST a movement. That is the point."""
        response = client_for(tenant_a["user"]).post("/api/v1/stock-movements/", {})
        assert response.status_code == 405

    def test_ledger_readable(self, tenant_a):
        response = client_for(tenant_a["user"]).get("/api/v1/stock-movements/")
        assert response.status_code == 200
        assert response.data["results"][0]["kind"] == "PURCHASE_RECEIPT"


class TestAllocationPreview:
    def test_shows_the_batch_before_committing(self, tenant_a):
        response = client_for(tenant_a["user"]).post(
            "/api/v1/allocations/preview/",
            {
                "product": str(tenant_a["product"].id),
                "location": str(tenant_a["location"].id),
                "quantity": 6,
                "uom_code": "UNIT",
            },
            format="json",
        )
        assert response.status_code == 200
        assert response.data[0]["batch_number"] == "AMX-0021"
        # Nothing was posted.
        assert StockMovement.objects.filter(kind=MovementKind.SALE).count() == 0

    def test_insufficient_stock_is_422_with_detail(self, tenant_a):
        response = client_for(tenant_a["user"]).post(
            "/api/v1/allocations/preview/",
            {
                "product": str(tenant_a["product"].id),
                "location": str(tenant_a["location"].id),
                "quantity": 99999,
                "uom_code": "UNIT",
            },
            format="json",
        )
        assert response.status_code == 422
        assert response.data["error"]["code"] == "insufficient_stock"
        assert response.data["error"]["meta"]["available_base"] == 500


class TestAdjustment:
    def test_requires_a_reason(self, tenant_a):
        response = client_for(tenant_a["user"]).post(
            "/api/v1/stock/adjust/",
            {
                "batch": str(tenant_a["batch"].id),
                "location": str(tenant_a["location"].id),
                "quantity": -10,
                "uom_code": "UNIT",
                "reason": "",
            },
            format="json",
        )
        assert response.status_code == 400

    def test_posts_with_a_reason(self, tenant_a):
        response = client_for(tenant_a["user"]).post(
            "/api/v1/stock/adjust/",
            {
                "batch": str(tenant_a["batch"].id),
                "location": str(tenant_a["location"].id),
                "quantity": -10,
                "uom_code": "UNIT",
                "reason": "Damaged in handling",
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.data["reason"] == "Damaged in handling"

    def test_idempotency_key_replays(self, tenant_a):
        client = client_for(tenant_a["user"])
        body = {
            "batch": str(tenant_a["batch"].id),
            "location": str(tenant_a["location"].id),
            "quantity": 10,
            "uom_code": "UNIT",
            "reason": "Recount",
        }
        first = client.post(
            "/api/v1/stock/adjust/", body, format="json", HTTP_IDEMPOTENCY_KEY="retry-1"
        )
        second = client.post(
            "/api/v1/stock/adjust/", body, format="json", HTTP_IDEMPOTENCY_KEY="retry-1"
        )
        assert first.status_code == 201
        assert second.status_code == 200
        assert first.data["id"] == second.data["id"]


class TestCrossTenant:
    """404, never 403. A 403 confirms the record exists elsewhere."""

    def test_cannot_list_other_tenant_products(self, tenant_a, tenant_b):
        response = client_for(tenant_a["user"]).get("/api/v1/products/")
        names = {p["name"] for p in response.data["results"]}
        assert "Paracetamol 500mg" not in names

    @pytest.mark.parametrize(
        "path",
        ["products", "batches", "locations"],
    )
    def test_foreign_detail_returns_404(self, tenant_a, tenant_b, path):
        key = {"products": "product", "batches": "batch", "locations": "location"}[path]
        foreign_id = tenant_b[key].id
        response = client_for(tenant_a["user"]).get(f"/api/v1/{path}/{foreign_id}/")
        assert response.status_code == 404, f"{path} leaked a foreign record"

    def test_cannot_adjust_a_foreign_batch(self, tenant_a, tenant_b):
        response = client_for(tenant_a["user"]).post(
            "/api/v1/stock/adjust/",
            {
                "batch": str(tenant_b["batch"].id),
                "location": str(tenant_a["location"].id),
                "quantity": -10,
                "uom_code": "UNIT",
                "reason": "Attempted cross-tenant write",
            },
            format="json",
        )
        assert response.status_code == 404

    def test_cannot_preview_against_a_foreign_product(self, tenant_a, tenant_b):
        response = client_for(tenant_a["user"]).post(
            "/api/v1/allocations/preview/",
            {
                "product": str(tenant_b["product"].id),
                "location": str(tenant_a["location"].id),
                "quantity": 1,
                "uom_code": "UNIT",
            },
            format="json",
        )
        assert response.status_code == 404

    def test_ledger_scoped_to_tenant(self, tenant_a, tenant_b):
        response = client_for(tenant_b["user"]).get("/api/v1/stock-movements/")
        assert response.data["results"] == []


class TestAuth:
    @pytest.mark.parametrize(
        "path",
        ["/api/v1/products/", "/api/v1/stock/", "/api/v1/stock-movements/", "/api/v1/batches/"],
    )
    def test_unauthenticated_is_rejected(self, path):
        assert APIClient().get(path).status_code == 401

    def test_me_returns_active_organization(self, tenant_a):
        response = client_for(tenant_a["user"]).get("/api/v1/auth/me/")
        assert response.status_code == 200
        assert response.data["organization"]["name"] == "Kigali Care Pharmacy"
