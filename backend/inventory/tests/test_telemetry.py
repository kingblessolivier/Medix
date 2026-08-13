"""Cold chain: excursions, quarantine, and the silence that looks like safety.

The test that matters most is
`test_an_excursion_quarantines_cold_chain_stock`. This is the one alert
in the system that acts rather than warns: by the time a person reads a
temperature warning the product is already damaged, and what the system
can still do is stop it being sold.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import User
from core.quantity import Quantity
from inventory import services, telemetry
from inventory.models import MovementKind, StockStatus, TemperatureClass
from inventory.telemetry import Excursion, Reading, Sensor
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db

NOW = timezone.now()


@pytest.fixture
def fridge():
    org = make_org("Kigali Care")
    user = User.objects.create_user(username="marie", password="x", organization=org)
    location = make_location(
        org, "Vaccine fridge", "FRIDGE", temperature_class=TemperatureClass.COLD
    )
    sensor = Sensor.objects.create(
        organization=org, location=location, device_code="FRIDGE-1", name="Fridge probe"
    )

    cold = make_product(org, "Insulin", cold_chain=True)
    batch = make_batch(org, cold, number="INS-1", cold_chain=True)
    services.post_movement(
        organization=org,
        location=location,
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        quantity=Quantity(10, uom(cold, "PACK")),
    )
    return {
        "org": org, "user": user, "location": location, "sensor": sensor,
        "product": cold, "batch": batch,
    }


def readings(fridge, temperatures, *, start=None, every=5):
    """A run of readings, `every` minutes apart, ending now."""
    start = start or NOW - timedelta(minutes=every * len(temperatures))
    results = []
    for index, celsius in enumerate(temperatures):
        results.append(
            telemetry.record_reading(
                sensor=fridge["sensor"],
                celsius=celsius,
                recorded_at=start + timedelta(minutes=every * index),
                performed_by=fridge["user"],
            )
        )
    return results


def available(fridge):
    return services.balance_for(
        organization=fridge["org"],
        product=fridge["product"],
        location=fridge["location"],
    )


def held(fridge):
    return services.balance_for(
        organization=fridge["org"],
        product=fridge["product"],
        location=fridge["location"],
        status=StockStatus.QUARANTINED,
    )


class TestReadings:
    def test_a_reading_in_range_opens_nothing(self, fridge):
        readings(fridge, [4.0, 4.2, 3.9])
        assert not Excursion.objects.exists()
        assert available(fridge) == 1_000

    def test_the_log_is_append_only(self, fridge):
        readings(fridge, [4.0])
        reading = Reading.objects.first()
        with pytest.raises(RuntimeError):
            reading.delete()

    def test_a_repeat_at_the_same_time_is_ignored(self, fridge):
        """The agent re-sends its buffer; that must not double-count."""
        moment = NOW - timedelta(minutes=5)
        first = telemetry.record_reading(
            sensor=fridge["sensor"], celsius=4.0, recorded_at=moment
        )
        again = telemetry.record_reading(
            sensor=fridge["sensor"], celsius=4.0, recorded_at=moment
        )
        assert not first.duplicate
        assert again.duplicate
        assert Reading.objects.count() == 1

    def test_last_seen_tracks_the_newest_reading(self, fridge):
        readings(fridge, [4.0, 4.1])
        fridge["sensor"].refresh_from_db()
        assert fridge["sensor"].last_seen_at is not None


class TestGraceWindow:
    def test_one_reading_out_of_range_is_a_door_being_opened(self, fridge):
        readings(fridge, [4.0, 9.5])
        assert not Excursion.objects.exists()
        assert available(fridge) == 1_000

    def test_a_brief_run_inside_the_window_does_not_open_one(self, fridge):
        # Three readings over ten minutes; the window is thirty.
        readings(fridge, [4.0, 9.1, 9.4, 9.2], every=3)
        assert not Excursion.objects.exists()

    def test_a_sustained_run_past_the_window_opens_one(self, fridge):
        # Eight readings five minutes apart is thirty-five minutes out.
        readings(fridge, [9.1] * 8, every=5)
        assert Excursion.objects.count() == 1

    def test_recovering_before_the_window_closes_nothing(self, fridge):
        readings(fridge, [9.1, 9.2, 4.0, 4.1], every=5)
        assert not Excursion.objects.exists()


class TestQuarantine:
    def test_an_excursion_quarantines_cold_chain_stock(self, fridge):
        """It acts rather than warns. That is the whole point."""
        readings(fridge, [9.1] * 8, every=5)

        assert available(fridge) == 0
        assert held(fridge) == 1_000

    def test_the_excursion_records_what_it_held(self, fridge):
        readings(fridge, [9.1] * 8, every=5)
        excursion = Excursion.objects.get()
        assert excursion.quarantined_base == 1_000
        assert excursion.batches_affected == 1
        assert excursion.peak_celsius == Decimal("9.1")

    def test_stock_that_is_not_cold_chain_is_left_alone(self, fridge):
        """Quarantining the plasters would bury the insulin."""
        plain = make_product(fridge["org"], "Bandages", cold_chain=False)
        batch = make_batch(fridge["org"], plain, number="BAN-1")
        services.post_movement(
            organization=fridge["org"],
            location=fridge["location"],
            batch=batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(5, uom(plain, "PACK")),
        )
        readings(fridge, [9.1] * 8, every=5)

        assert services.balance_for(
            organization=fridge["org"], product=plain, location=fridge["location"]
        ) == 500

    def test_quarantined_stock_cannot_be_allocated(self, fridge):
        from core.exceptions import InsufficientStock

        readings(fridge, [9.1] * 8, every=5)
        with pytest.raises(InsufficientStock):
            services.allocate_fefo(
                organization=fridge["org"],
                product=fridge["product"],
                location=fridge["location"],
                quantity=Quantity(1, uom(fridge["product"], "PACK")),
            )

    def test_a_second_excursion_does_not_open_while_one_is_open(self, fridge):
        readings(fridge, [9.1] * 12, every=5)
        assert Excursion.objects.count() == 1


class TestRecovery:
    def test_returning_to_range_closes_the_excursion(self, fridge):
        readings(fridge, [9.1] * 8 + [4.0], every=5)
        excursion = Excursion.objects.get()
        assert not excursion.is_open

    def test_closing_does_not_release_the_stock(self, fridge):
        """The fridge recovered. Whether the batch is safe is a judgement."""
        readings(fridge, [9.1] * 8 + [4.0], every=5)
        assert held(fridge) == 1_000
        assert available(fridge) == 0

    def test_resolving_records_what_was_decided(self, fridge):
        readings(fridge, [9.1] * 8 + [4.0], every=5)
        excursion = telemetry.resolve_excursion(
            excursion=Excursion.objects.get(),
            performed_by=fridge["user"],
            resolution="Manufacturer confirmed 12h excursion tolerance. Released.",
        )
        assert excursion.resolved_at is not None

    def test_a_resolution_needs_words(self, fridge):
        from core.exceptions import DomainError

        readings(fridge, [9.1] * 8, every=5)
        with pytest.raises(DomainError):
            telemetry.resolve_excursion(
                excursion=Excursion.objects.get(),
                performed_by=fridge["user"],
                resolution="  ",
            )

    def test_release_puts_it_back(self, fridge):
        from commerce import services as commerce

        readings(fridge, [9.1] * 8 + [4.0], every=5)
        commerce.release_batch(
            batch=fridge["batch"],
            location=fridge["location"],
            organization=fridge["org"],
            performed_by=fridge["user"],
            reason="Within the manufacturer's stated excursion tolerance.",
        )
        assert available(fridge) == 1_000
        assert held(fridge) == 0


class TestAlerts:
    def test_an_open_excursion_is_critical(self, fridge):
        readings(fridge, [9.1] * 8, every=5)
        found = telemetry.checks(organization=fridge["org"])
        assert [alert.code for alert in found] == ["COLD_CHAIN_EXCURSION"]
        assert found[0].severity == "CRITICAL"

    def test_a_silent_sensor_is_reported(self, fridge):
        """Silence is not safety — it is a fridge nobody is watching."""
        telemetry.record_reading(
            sensor=fridge["sensor"],
            celsius=4.0,
            recorded_at=NOW - timedelta(hours=6),
        )
        found = telemetry.checks(organization=fridge["org"])
        assert [alert.code for alert in found] == ["SENSOR_SILENT"]
        assert found[0].meta["hours"] >= 6

    def test_a_recent_sensor_is_not_reported(self, fridge):
        telemetry.record_reading(
            sensor=fridge["sensor"], celsius=4.0, recorded_at=NOW
        )
        assert telemetry.checks(organization=fridge["org"]) == []
