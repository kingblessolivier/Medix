"""Replay from the site agent.

The property under test throughout is that **a retry is free**. The agent
journals locally, sends when it can, and deletes nothing until the server
acknowledges — which means the server sees repeats routinely, not
exceptionally. If a repeat could double-post a sale or double-count a
reading, the offline path would be worse than no offline path at all.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from core import sync
from core.exceptions import DomainError
from core.models import Branch, Device, SyncEnvelope, User
from core.quantity import Quantity
from core.sync import SyncStatus
from inventory import services as inventory
from inventory.models import MovementKind
from inventory.telemetry import Reading, Sensor
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom
from sales.models import Sale, SaleStatus

pytestmark = pytest.mark.django_db

NOW = timezone.now()


@pytest.fixture
def till():
    org = make_org("Kigali Care")
    branch = Branch.objects.create(organization=org, name="Main", code="MAIN")
    user = User.objects.create_user(username="cashier", password="x", organization=org)
    location = make_location(org, "Shop floor", "FLOOR")
    location.branch = branch
    location.save(update_fields=["branch"])

    device = Device.objects.create(organization=org, code="TILL-1", name="Front till")

    product = make_product(org, "Paracetamol 500mg", legal_status="OTC")
    batch = make_batch(org, product, number="PAR-1")
    inventory.post_movement(
        organization=org,
        location=location,
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        quantity=Quantity(20, uom(product, "PACK")),
    )
    return {
        "org": org, "branch": branch, "user": user, "location": location,
        "device": device, "product": product, "batch": batch,
    }


def sale_payload(till, *, quantity=2, unit_price=500_00, paid=None):
    total = quantity * unit_price
    return {
        "location": str(till["location"].id),
        "lines": [
            {
                "product": str(till["product"].id),
                "quantity": quantity,
                "uom_code": "PACK",
                "unit_price": unit_price,
            }
        ],
        "payments": [{"method": "CASH", "amount": total if paid is None else paid}],
    }


def send(till, *, client_id, kind="sale", payload=None, sequence=1, occurred_at=None):
    return sync.apply_envelope(
        organization=till["org"],
        device=till["device"],
        client_id=client_id,
        kind=kind,
        payload=payload if payload is not None else sale_payload(till),
        occurred_at=occurred_at or (NOW - timedelta(hours=2)),
        sequence=sequence,
        performed_by=till["user"],
    )


class TestIdempotency:
    def test_an_envelope_applies_once(self, till):
        applied = send(till, client_id="a-1")
        assert not applied.replayed
        assert applied.envelope.status == SyncStatus.APPLIED
        assert Sale.objects.count() == 1

    def test_the_same_envelope_twice_changes_nothing(self, till):
        send(till, client_id="a-1")
        again = send(till, client_id="a-1")

        assert again.replayed
        assert Sale.objects.count() == 1
        assert SyncEnvelope.objects.count() == 1

    def test_a_repeat_returns_the_first_answer(self, till):
        first = send(till, client_id="a-1")
        again = send(till, client_id="a-1")
        assert again.envelope.result == first.envelope.result

    def test_stock_leaves_once(self, till):
        before = inventory.balance_for(
            organization=till["org"], product=till["product"], location=till["location"]
        )
        send(till, client_id="a-1")
        send(till, client_id="a-1")

        after = inventory.balance_for(
            organization=till["org"], product=till["product"], location=till["location"]
        )
        assert before - after == 200  # two packs of 100

    def test_two_devices_do_not_collide_on_sequence(self, till):
        """Order matters within a device, not between them."""
        second = Device.objects.create(
            organization=till["org"], code="TILL-2", name="Back till"
        )
        send(till, client_id="a-1", sequence=1)
        sync.apply_envelope(
            organization=till["org"],
            device=second,
            client_id="b-1",
            kind="sale",
            payload=sale_payload(till),
            occurred_at=NOW,
            sequence=1,
            performed_by=till["user"],
        )
        assert SyncEnvelope.objects.count() == 2


class TestTheWhitelist:
    def test_an_unknown_kind_is_refused(self, till):
        with pytest.raises(DomainError) as raised:
            send(till, client_id="a-1", kind="delete_everything", payload={})
        assert raised.value.code == "unknown_sync_kind"

    def test_a_refused_kind_records_nothing(self, till):
        with pytest.raises(DomainError):
            send(till, client_id="a-1", kind="core.models.User.objects.all", payload={})
        assert not SyncEnvelope.objects.exists()

    def test_the_whitelist_is_the_only_route(self):
        """A dotted path in a payload must never reach import_string."""
        assert set(sync.HANDLERS) == {"sale", "temperature", "stock_count"}


class TestFailure:
    def test_a_payload_that_cannot_apply_is_kept(self, till):
        """Dropping it would lose the only copy — the device deleted its own."""
        applied = send(
            till,
            client_id="a-1",
            payload={"location": str(till["location"].id), "lines": []},
        )
        assert applied.envelope.status == SyncStatus.FAILED
        assert applied.envelope.error
        assert applied.envelope.payload["lines"] == []

    def test_a_failure_does_not_take_the_batch_with_it(self, till):
        """One bad envelope in a batch must not roll back the good ones."""
        send(till, client_id="good-1")
        send(
            till,
            client_id="bad-1",
            payload={"location": str(till["location"].id), "lines": []},
        )
        send(till, client_id="good-2")

        assert Sale.objects.filter(status=SaleStatus.COMPLETED).count() == 2
        assert SyncEnvelope.objects.filter(status=SyncStatus.FAILED).count() == 1

    def test_a_failure_is_audited(self, till):
        from core.models import AuditEvent

        send(
            till,
            client_id="a-1",
            payload={"location": str(till["location"].id), "lines": []},
        )
        assert AuditEvent.objects.filter(action="core.sync.failed").exists()

    def test_retrying_a_failure_does_not_rerun_it(self, till):
        """The client id is spent. A fixed payload needs a new one."""
        send(
            till,
            client_id="a-1",
            payload={"location": str(till["location"].id), "lines": []},
        )
        again = send(till, client_id="a-1")
        assert again.replayed
        assert not Sale.objects.exists()


class TestSaleReplay:
    def test_the_sale_keeps_the_hour_it_happened_in(self, till):
        """A sale made during an outage belongs to when it was made."""
        when = NOW - timedelta(hours=9)
        send(till, client_id="a-1", occurred_at=when)
        assert Sale.objects.get().occurred_at == when

    def test_tender_taken_at_the_till_settles_the_sale(self, till):
        send(till, client_id="a-1")
        assert Sale.objects.get().status == SaleStatus.COMPLETED

    def test_a_part_payment_leaves_it_pending(self, till):
        send(till, client_id="a-1", payload=sale_payload(till, paid=100_00))
        assert Sale.objects.get().status == SaleStatus.PENDING_PAYMENT

    def test_an_offline_sale_meets_the_prescription_gate(self, till):
        """The offline path must not be the way around the rules."""
        pom = make_product(till["org"], "Amoxicillin 500mg")
        batch = make_batch(till["org"], pom, number="AMX-1")
        inventory.post_movement(
            organization=till["org"],
            location=till["location"],
            batch=batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(5, uom(pom, "PACK")),
        )
        payload = sale_payload(till)
        payload["lines"][0]["product"] = str(pom.id)

        applied = send(till, client_id="a-1", payload=payload)
        assert applied.envelope.status == SyncStatus.FAILED
        assert "rescription" in applied.envelope.error

    def test_stock_it_does_not_have_fails_rather_than_goes_negative(self, till):
        applied = send(till, client_id="a-1", payload=sale_payload(till, quantity=999))
        assert applied.envelope.status == SyncStatus.FAILED


class TestTemperatureReplay:
    def test_a_buffer_of_readings_is_stored(self, till):
        sensor = Sensor.objects.create(
            organization=till["org"],
            location=till["location"],
            device_code="FRIDGE-1",
            name="Fridge",
        )
        payload = {
            "sensor": "FRIDGE-1",
            "readings": [
                {"at": (NOW - timedelta(minutes=n)).isoformat(), "celsius": 4.0}
                for n in (30, 25, 20)
            ],
        }
        applied = send(till, client_id="t-1", kind="temperature", payload=payload)
        assert applied.envelope.result == {"stored": 3, "excursions_opened": 0}
        assert Reading.objects.filter(sensor=sensor).count() == 3

    def test_an_overlapping_buffer_adds_only_what_is_new(self, till):
        Sensor.objects.create(
            organization=till["org"],
            location=till["location"],
            device_code="FRIDGE-1",
            name="Fridge",
        )
        times = [NOW - timedelta(minutes=n) for n in (30, 25, 20, 15)]
        send(
            till,
            client_id="t-1",
            kind="temperature",
            payload={
                "sensor": "FRIDGE-1",
                "readings": [{"at": t.isoformat(), "celsius": 4.0} for t in times[:3]],
            },
        )
        applied = send(
            till,
            client_id="t-2",
            kind="temperature",
            payload={
                "sensor": "FRIDGE-1",
                "readings": [{"at": t.isoformat(), "celsius": 4.0} for t in times[1:]],
            },
        )
        assert applied.envelope.result["stored"] == 1
        assert Reading.objects.count() == 4

    def test_an_unknown_sensor_is_refused(self, till):
        applied = send(
            till,
            client_id="t-1",
            kind="temperature",
            payload={"sensor": "NOT-A-FRIDGE", "readings": []},
        )
        assert applied.envelope.status == SyncStatus.FAILED
        assert "unknown" in applied.envelope.error.lower()


class TestStockCountReplay:
    def counted(self, till, base):
        return {
            "location": str(till["location"].id),
            "counts": [
                {"batch": str(till["batch"].id), "counted_base": base, "reason": "Count"}
            ],
        }

    def test_a_count_that_matches_writes_nothing(self, till):
        applied = send(
            till, client_id="c-1", kind="stock_count", payload=self.counted(till, 2000)
        )
        assert applied.envelope.result == {"adjustments": 0}

    def test_a_shortfall_becomes_an_adjustment(self, till):
        """The count is not the record. The difference is."""
        send(till, client_id="c-1", kind="stock_count", payload=self.counted(till, 1900))

        assert inventory.balance_for(
            organization=till["org"],
            product=till["product"],
            location=till["location"],
        ) == 1900
        assert till["batch"].movements.filter(kind=MovementKind.ADJUSTMENT).count() == 1

    def test_a_surplus_becomes_an_adjustment_too(self, till):
        send(till, client_id="c-1", kind="stock_count", payload=self.counted(till, 2100))
        assert inventory.balance_for(
            organization=till["org"],
            product=till["product"],
            location=till["location"],
        ) == 2100


class TestDevice:
    def test_a_device_is_seen_when_it_syncs(self, till):
        assert till["device"].last_seen_at is None
        send(till, client_id="a-1")
        till["device"].refresh_from_db()
        assert till["device"].last_seen_at is not None

    def test_a_device_code_is_unique_within_an_organization(self, till):
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            Device.objects.create(organization=till["org"], code="TILL-1")
