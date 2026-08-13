"""The endpoints the site agent talks to.

The agent is the least trusted input in the system — a machine in a
pharmacy, offline for hours, replaying whatever it buffered. So the
contract tested here is narrow on purpose: a known device, a whitelisted
kind, an answer per envelope, and nothing readable across a tenant line.
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Branch, Device, User
from core.quantity import Quantity
from inventory import services
from inventory.models import MovementKind, TemperatureClass
from inventory.telemetry import Excursion, Reading, Sensor
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom
from sales.models import Sale

pytestmark = pytest.mark.django_db

NOW = timezone.now()


def tenant(name, code, *, kind="RETAIL", username="user"):
    org = make_org(name, kind=kind)
    branch = Branch.objects.create(organization=org, name="Main", code="MAIN")
    user = User.objects.create_user(username=username, password="x", organization=org)
    location = make_location(org, f"{name} store", code)
    location.branch = branch
    location.save(update_fields=["branch"])

    device = Device.objects.create(organization=org, code=f"{code}-TILL", name="Till")
    sensor = Sensor.objects.create(
        organization=org, location=location, device_code=f"{code}-FRIDGE", name="Fridge"
    )

    product = make_product(org, "Paracetamol 500mg", legal_status="OTC")
    batch = make_batch(org, product, number=f"{code}-1")
    services.post_movement(
        organization=org,
        location=location,
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        quantity=Quantity(20, uom(product, "PACK")),
    )

    client = APIClient()
    client.force_authenticate(user=user)
    return {
        "org": org, "user": user, "location": location, "device": device,
        "sensor": sensor, "product": product, "batch": batch, "client": client,
    }


@pytest.fixture
def site():
    return tenant("Kigali Care", "KC")


@pytest.fixture
def other():
    return tenant("ABC Wholesale", "ABC", kind="WHOLESALE", username="jean")


def envelope(site, *, client_id="a-1", kind="sale", payload=None, sequence=1):
    return {
        "client_id": client_id,
        "kind": kind,
        "sequence": sequence,
        "occurred_at": (NOW - timedelta(hours=2)).isoformat(),
        "payload": payload if payload is not None else {
            "location": str(site["location"].id),
            "lines": [
                {
                    "product": str(site["product"].id),
                    "quantity": 1,
                    "uom_code": "PACK",
                    "unit_price": 500_00,
                }
            ],
            "payments": [{"method": "CASH", "amount": 500_00}],
        },
    }


def post_sync(site, envelopes, **extra):
    return site["client"].post(
        "/api/v1/sync/",
        {"device": site["device"].code, "envelopes": envelopes, **extra},
        format="json",
    )


class TestSync:
    def test_a_batch_is_answered_per_envelope(self, site):
        response = post_sync(
            site, [envelope(site, client_id="a-1"), envelope(site, client_id="a-2")]
        )
        assert response.status_code == 200
        assert response.data["accepted"] == 2
        assert [r["status"] for r in response.data["results"]] == ["APPLIED", "APPLIED"]

    def test_a_replay_answers_duplicate_without_reapplying(self, site):
        post_sync(site, [envelope(site)])
        response = post_sync(site, [envelope(site)])

        assert response.data["results"][0]["status"] == "DUPLICATE"
        assert Sale.objects.count() == 1

    def test_one_bad_envelope_does_not_fail_the_batch(self, site):
        """The agent clears what applied and keeps what did not."""
        response = post_sync(
            site,
            [
                envelope(site, client_id="a-1"),
                envelope(
                    site,
                    client_id="a-2",
                    payload={"location": str(site["location"].id), "lines": []},
                ),
                envelope(site, client_id="a-3"),
            ],
        )
        assert [r["status"] for r in response.data["results"]] == [
            "APPLIED",
            "FAILED",
            "APPLIED",
        ]
        assert Sale.objects.count() == 2

    def test_a_failure_says_why(self, site):
        response = post_sync(
            site,
            [
                envelope(
                    site,
                    payload={"location": str(site["location"].id), "lines": []},
                )
            ],
        )
        assert response.data["results"][0]["error"]

    def test_an_unknown_device_is_refused(self, site):
        response = site["client"].post(
            "/api/v1/sync/",
            {"device": "NOT-A-TILL", "envelopes": [envelope(site)]},
            format="json",
        )
        assert response.status_code == 422
        assert response.data["error"]["code"] == "unknown_device"

    def test_a_deactivated_device_is_refused(self, site):
        site["device"].is_active = False
        site["device"].save(update_fields=["is_active"])

        assert post_sync(site, [envelope(site)]).status_code == 422

    def test_another_tenants_device_is_not_visible(self, site, other):
        """The code exists — just not here. It must read as unknown."""
        response = site["client"].post(
            "/api/v1/sync/",
            {"device": other["device"].code, "envelopes": []},
            format="json",
        )
        assert response.status_code == 422

    def test_the_agent_version_is_recorded(self, site):
        post_sync(site, [envelope(site)], agent_version="0.1.0")
        site["device"].refresh_from_db()
        assert site["device"].agent_version == "0.1.0"

    def test_anonymous_cannot_sync(self, site):
        assert APIClient().post("/api/v1/sync/", {}, format="json").status_code in (
            401,
            403,
        )


class TestTelemetry:
    def readings(self, temperatures, *, every=5, buffered=False):
        return [
            {
                "celsius": celsius,
                "at": (NOW - timedelta(minutes=every * (len(temperatures) - n))).isoformat(),
                "buffered": buffered,
            }
            for n, celsius in enumerate(temperatures)
        ]

    def post(self, site, readings, sensor=None):
        return site["client"].post(
            "/api/v1/telemetry/",
            {"sensor": sensor or site["sensor"].device_code, "readings": readings},
            format="json",
        )

    def test_readings_are_stored(self, site):
        response = self.post(site, self.readings([4.0, 4.1, 3.9]))
        assert response.status_code == 201
        assert response.data["stored"] == 3
        assert Reading.objects.count() == 3

    def test_a_re_sent_buffer_is_counted_as_duplicate(self, site):
        readings = self.readings([4.0, 4.1])
        self.post(site, readings)
        response = self.post(site, readings)

        assert response.data == {"stored": 0, "duplicates": 2, "excursions_opened": []}

    def test_a_sustained_excursion_is_reported_back(self, site):
        cold = make_product(site["org"], "Insulin", cold_chain=True)
        batch = make_batch(site["org"], cold, number="INS-1", cold_chain=True)
        site["location"].temperature_class = TemperatureClass.COLD
        site["location"].save(update_fields=["temperature_class"])
        services.post_movement(
            organization=site["org"],
            location=site["location"],
            batch=batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(4, uom(cold, "PACK")),
        )

        response = self.post(site, self.readings([9.5] * 8))

        assert len(response.data["excursions_opened"]) == 1
        assert response.data["excursions_opened"][0]["quarantined_base"] == 400

    def test_an_unknown_sensor_is_refused(self, site):
        response = self.post(site, [], sensor="NOT-A-FRIDGE")
        assert response.status_code == 422
        assert response.data["error"]["code"] == "unknown_sensor"

    def test_another_tenants_sensor_is_not_visible(self, site, other):
        assert self.post(site, [], sensor=other["sensor"].device_code).status_code == 422

    def test_a_buffered_reading_is_marked_as_such(self, site):
        """A late reading was never actionable, and must not look like one."""
        self.post(site, self.readings([4.0], buffered=True))
        assert Reading.objects.get().was_buffered


class TestExcursions:
    @pytest.fixture
    def excursion(self, site):
        site["location"].temperature_class = TemperatureClass.COLD
        site["location"].save(update_fields=["temperature_class"])
        for n in range(8):
            site["client"].post(
                "/api/v1/telemetry/",
                {
                    "sensor": site["sensor"].device_code,
                    "readings": [
                        {"celsius": 9.5, "at": (NOW - timedelta(minutes=40 - n * 5)).isoformat()}
                    ],
                },
                format="json",
            )
        return Excursion.objects.get()

    def test_they_are_listed(self, site, excursion):
        response = site["client"].get("/api/v1/excursions/")
        assert response.status_code == 200
        assert response.data["count"] == 1

    def test_open_ones_can_be_filtered(self, site, excursion):
        response = site["client"].get("/api/v1/excursions/?open=true")
        assert response.data["count"] == 1

    def test_they_cannot_be_created_by_hand(self, site):
        """A sensor records one. A person does not."""
        response = site["client"].post("/api/v1/excursions/", {}, format="json")
        assert response.status_code == 405

    def test_resolving_records_the_decision(self, site, excursion):
        response = site["client"].post(
            f"/api/v1/excursions/{excursion.id}/resolve/",
            {"resolution": "Manufacturer confirms 12h tolerance. Batch released."},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["resolved_at"]

    def test_an_empty_resolution_is_refused(self, site, excursion):
        response = site["client"].post(
            f"/api/v1/excursions/{excursion.id}/resolve/", {"resolution": ""},
            format="json",
        )
        assert response.status_code == 422

    def test_another_tenant_cannot_see_it(self, site, excursion, other):
        assert other["client"].get("/api/v1/excursions/").data["count"] == 0

    def test_another_tenant_cannot_resolve_it(self, site, excursion, other):
        response = other["client"].post(
            f"/api/v1/excursions/{excursion.id}/resolve/",
            {"resolution": "Not mine to decide."},
            format="json",
        )
        assert response.status_code == 404


class TestDevicesAndSensors:
    def test_devices_are_listed(self, site):
        response = site["client"].get("/api/v1/devices/")
        assert [d["code"] for d in response.data["results"]] == [site["device"].code]

    def test_a_device_can_be_registered(self, site):
        response = site["client"].post(
            "/api/v1/devices/", {"code": "TILL-2", "name": "Back till"}, format="json"
        )
        assert response.status_code == 201
        assert Device.objects.filter(organization=site["org"], code="TILL-2").exists()

    def test_a_sensor_can_be_registered(self, site):
        response = site["client"].post(
            "/api/v1/sensors/",
            {
                "location": str(site["location"].id),
                "device_code": "FRIDGE-2",
                "name": "Second fridge",
                "minimum_c": "2.0",
                "maximum_c": "8.0",
            },
            format="json",
        )
        assert response.status_code == 201

    def test_another_tenants_devices_are_invisible(self, site, other):
        codes = [d["code"] for d in site["client"].get("/api/v1/devices/").data["results"]]
        assert other["device"].code not in codes

    def test_another_tenants_sensors_are_invisible(self, site, other):
        codes = [
            s["device_code"]
            for s in site["client"].get("/api/v1/sensors/").data["results"]
        ]
        assert other["sensor"].device_code not in codes


class TestSyncStatus:
    def test_it_reports_what_the_agents_have_been_doing(self, site):
        post_sync(site, [envelope(site)])
        response = site["client"].get("/api/v1/sync/status/")

        assert response.status_code == 200
        assert response.data["devices"][0]["code"] == site["device"].code
        assert response.data["devices"][0]["silent_hours"] == 0

    def test_a_device_that_has_never_reported_says_so(self, site):
        response = site["client"].get("/api/v1/sync/status/")
        assert response.data["devices"][0]["silent_hours"] is None

    def test_failures_are_listed_rather_than_dropped(self, site):
        post_sync(
            site,
            [envelope(site, payload={"location": str(site["location"].id), "lines": []})],
        )
        response = site["client"].get("/api/v1/sync/status/")

        assert len(response.data["failures"]) == 1
        assert response.data["failures"][0]["kind"] == "sale"

    def test_another_tenants_failures_are_not_shown(self, site, other):
        post_sync(
            site,
            [envelope(site, payload={"location": str(site["location"].id), "lines": []})],
        )
        assert other["client"].get("/api/v1/sync/status/").data["failures"] == []
