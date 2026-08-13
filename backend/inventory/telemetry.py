"""Cold-chain telemetry: readings, excursions, and automatic quarantine.

A fridge that drifts to 12°C overnight does not announce itself. By the
time anyone opens it the insulin inside is already unusable, and the only
question left is whether anybody knows. So this is the one alert in the
system that **acts rather than warns**: an excursion quarantines the
stock in that location and someone has to release it deliberately.

Two properties the design turns on.

**Readings are append-only.** A temperature log a pharmacy can edit is
not evidence, and the log is exactly what a regulator asks for after a
cold-chain complaint.

**An excursion is a period, not a point.** One reading at 8.4°C is a
door being opened; forty minutes at 8.4°C is a batch to quarantine. The
grace window is effective-dated configuration, because it is a policy
decision and different products tolerate different lapses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import models, transaction
from django.utils import timezone

from core import audit
from core.alerts import Alert, Severity, about, rule_for
from core.exceptions import DomainError
from core.models import BaseModel, Organization, TenantModel, User


class Sensor(TenantModel):
    """A probe in one location.

    Identified by a device code the agent reports, so a replaced probe in
    the same fridge continues the same location's history rather than
    starting a new one.
    """

    location = models.ForeignKey(
        "inventory.Location", on_delete=models.PROTECT, related_name="sensors"
    )
    device_code = models.CharField(max_length=60)
    name = models.CharField(max_length=120, blank=True)

    #: The range this location must stay inside. Defaults to 2–8°C, the
    #: cold-chain band, but a freezer or a controlled-room store sets its
    #: own — the location's temperature class is the wider statement and
    #: this is the number the sensor is judged against.
    minimum_c = models.DecimalField(max_digits=4, decimal_places=1, default=2)
    maximum_c = models.DecimalField(max_digits=4, decimal_places=1, default=8)

    is_active = models.BooleanField(default=True)
    #: Last time anything was heard from it. A sensor that stops
    #: reporting is its own kind of failure — silence is not safety.
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "inventory_sensor"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "device_code"], name="uq_sensor_device"
            ),
        ]
        indexes = [models.Index(fields=["organization", "location"])]

    def __str__(self) -> str:
        return self.name or self.device_code

    def is_outside(self, celsius) -> bool:
        return celsius < self.minimum_c or celsius > self.maximum_c


class Reading(BaseModel):
    """One measurement. Append-only.

    No update or delete path exists, for the same reason `StockMovement`
    has none: a log the pharmacy can edit is not evidence, and this log
    is what a regulator asks for after a complaint.
    """

    sensor = models.ForeignKey(Sensor, on_delete=models.PROTECT, related_name="readings")
    celsius = models.DecimalField(max_digits=4, decimal_places=1)
    recorded_at = models.DateTimeField()
    #: True when the agent buffered this offline and sent it later. The
    #: gap matters: a reading that arrived four hours late was not
    #: actionable when it was taken.
    was_buffered = models.BooleanField(default=False)

    class Meta:
        db_table = "inventory_reading"
        ordering = ["-recorded_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["sensor", "recorded_at"], name="uq_reading_at"
            ),
        ]
        indexes = [models.Index(fields=["sensor", "-recorded_at"])]

    def __str__(self) -> str:
        return f"{self.celsius}°C at {self.recorded_at:%d %b %H:%M}"

    def save(self, *args, **kwargs):
        if self.pk and Reading.objects.filter(pk=self.pk).exists():
            raise RuntimeError("The temperature log is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("The temperature log is append-only.")


class Excursion(TenantModel):
    """A period outside range, and what was done about it.

    Opened when readings stay out of range past the grace window, closed
    when they come back. The stock quarantined is recorded on the row so
    the release decision has the excursion in front of it.
    """

    sensor = models.ForeignKey(Sensor, on_delete=models.PROTECT, related_name="excursions")
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)

    peak_celsius = models.DecimalField(max_digits=4, decimal_places=1)
    minimum_celsius = models.DecimalField(max_digits=4, decimal_places=1)
    reading_count = models.IntegerField(default=0)

    quarantined_base = models.BigIntegerField(default=0)
    batches_affected = models.IntegerField(default=0)

    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution = models.TextField(blank=True)

    class Meta:
        db_table = "inventory_excursion"
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["organization", "-started_at"])]

    def __str__(self) -> str:
        return f"{self.sensor} from {self.started_at:%d %b %H:%M}"

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    @property
    def duration_minutes(self) -> int:
        end = self.ended_at or timezone.now()
        return int((end - self.started_at).total_seconds() // 60)


# --------------------------------------------------------------------------
# Recording
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordResult:
    reading: Reading | None
    excursion: Excursion | None
    opened: bool = False
    closed: bool = False
    quarantined_base: int = 0
    duplicate: bool = False


def _grace_minutes(organization: Organization) -> int:
    """How long out of range is a door being opened rather than a fault.

    Effective-dated configuration: it is a policy decision, and one
    pharmacy's fridge in a hot room is not another's.
    """
    return rule_for(organization=organization, code="COLD_CHAIN_EXCURSION")[
        "threshold"
    ].get("minutes", 30)


@transaction.atomic
def record_reading(
    *,
    sensor: Sensor,
    celsius,
    recorded_at: datetime | None = None,
    was_buffered: bool = False,
    performed_by: User | None = None,
) -> RecordResult:
    """Store one reading and act on what it means.

    Idempotent on (sensor, time): the agent re-sends its buffer after a
    reconnection and must not open a second excursion for readings the
    server already has.
    """
    recorded_at = recorded_at or timezone.now()
    organization = sensor.organization

    if Reading.objects.filter(sensor=sensor, recorded_at=recorded_at).exists():
        return RecordResult(None, None, duplicate=True)

    reading = Reading.objects.create(
        sensor=sensor,
        celsius=celsius,
        recorded_at=recorded_at,
        was_buffered=was_buffered,
    )
    # Written even when buffered: the point of last_seen_at is to catch a
    # sensor that has gone silent, and a late batch proves it was alive.
    sensor.last_seen_at = max(sensor.last_seen_at or recorded_at, recorded_at)
    sensor.save(update_fields=["last_seen_at", "modified_at"])

    open_excursion = Excursion.objects.filter(sensor=sensor, ended_at__isnull=True).first()

    if not sensor.is_outside(celsius):
        if open_excursion is None:
            return RecordResult(reading, None)
        return RecordResult(
            reading,
            _close(excursion=open_excursion, at=recorded_at, performed_by=performed_by),
            closed=True,
        )

    # Out of range.
    if open_excursion is not None:
        open_excursion.peak_celsius = max(open_excursion.peak_celsius, celsius)
        open_excursion.minimum_celsius = min(open_excursion.minimum_celsius, celsius)
        open_excursion.reading_count += 1
        open_excursion.save(
            update_fields=[
                "peak_celsius", "minimum_celsius", "reading_count", "modified_at"
            ]
        )
        return RecordResult(reading, open_excursion)

    # First out-of-range reading. An excursion only opens once the run has
    # lasted longer than the grace window — one reading is a door.
    grace = _grace_minutes(organization)
    since = recorded_at - timedelta(minutes=grace)
    run = list(
        Reading.objects.filter(
            sensor=sensor, recorded_at__gte=since, recorded_at__lte=recorded_at
        ).order_by("recorded_at")
    )
    if any(not sensor.is_outside(row.celsius) for row in run):
        return RecordResult(reading, None)
    if run[0].recorded_at > since:
        # Not enough history to prove the run lasted the whole window.
        return RecordResult(reading, None)

    excursion = _open(
        sensor=sensor, run=run, at=run[0].recorded_at, performed_by=performed_by
    )
    return RecordResult(
        reading,
        excursion,
        opened=True,
        quarantined_base=excursion.quarantined_base,
    )


def _open(*, sensor: Sensor, run, at, performed_by) -> Excursion:
    """Open an excursion and quarantine everything in the location.

    **Quarantine, not a warning.** By the time a person reads a warning
    the product is already damaged; what the system can still do is stop
    it being sold while somebody decides.
    """
    from inventory import movements
    from inventory.models import StockBalance, StockStatus

    temperatures = [row.celsius for row in run]
    excursion = Excursion.objects.create(
        organization=sensor.organization,
        sensor=sensor,
        started_at=at,
        peak_celsius=max(temperatures),
        minimum_celsius=min(temperatures),
        reading_count=len(run),
        created_by=performed_by,
    )

    reason = (
        f"Cold-chain excursion at {sensor}: "
        f"{excursion.minimum_celsius}–{excursion.peak_celsius}°C from {at:%d %b %H:%M}"
    )

    held = 0
    batches = 0
    balances = StockBalance.objects.filter(
        organization=sensor.organization,
        location=sensor.location,
        status=StockStatus.AVAILABLE,
        quantity_base__gt=0,
    ).select_related("batch__product")

    for balance in balances:
        # Only what the cold chain actually protects. Quarantining the
        # plasters stored in the same room would bury the insulin.
        if not balance.batch.product.cold_chain:
            continue
        from core.quantity import from_base

        movements.quarantine(
            organization=sensor.organization,
            batch=balance.batch,
            location=sensor.location,
            quantity=from_base(balance.quantity_base, balance.batch.product.base_uom),
            performed_by=performed_by,
            reason=reason,
        )
        held += balance.quantity_base
        batches += 1

    excursion.quarantined_base = held
    excursion.batches_affected = batches
    excursion.save(update_fields=["quarantined_base", "batches_affected", "modified_at"])

    audit.record(
        action="inventory.excursion.opened",
        subject=excursion,
        actor=performed_by,
        after={
            "sensor": str(sensor),
            "location": sensor.location.name,
            "peak_celsius": str(excursion.peak_celsius),
            "quarantined_base": held,
            "batches": batches,
        },
        organization=sensor.organization,
    )
    return excursion


def _close(*, excursion: Excursion, at, performed_by) -> Excursion:
    """Temperature is back. The stock stays quarantined.

    Closing records that the fridge recovered; it does not decide the
    goods are fine. That judgement belongs to a pharmacist, through
    `commerce.services.release_batch`, with a reason on the record.
    """
    excursion.ended_at = at
    excursion.save(update_fields=["ended_at", "modified_at"])

    audit.record(
        action="inventory.excursion.closed",
        subject=excursion,
        actor=performed_by,
        after={
            "duration_minutes": excursion.duration_minutes,
            "peak_celsius": str(excursion.peak_celsius),
            "still_quarantined_base": excursion.quarantined_base,
        },
        organization=excursion.organization,
    )
    return excursion


@transaction.atomic
def resolve_excursion(
    *, excursion: Excursion, performed_by: User, resolution: str
) -> Excursion:
    """Record what was decided about the stock. Not the same as closing.

    A temperature that recovered and a batch judged safe are two
    different facts, and only a person can assert the second.
    """
    if not resolution.strip():
        raise DomainError("Say what was decided.", code="resolution_required")

    excursion.resolved_at = timezone.now()
    excursion.resolution = resolution.strip()
    excursion.modified_by = performed_by
    excursion.save(
        update_fields=["resolved_at", "resolution", "modified_by", "modified_at"]
    )
    audit.record(
        action="inventory.excursion.resolved",
        subject=excursion,
        actor=performed_by,
        after={"resolution": resolution.strip()},
        organization=excursion.organization,
    )
    return excursion


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------


def checks(*, organization: Organization, as_of=None) -> list[Alert]:
    """Open excursions, and sensors that have gone quiet.

    Silence is its own failure. A probe that stopped reporting six hours
    ago is not a fridge that is fine — it is a fridge nobody is watching,
    and that reads the same on a dashboard unless somebody says so.
    """
    now = as_of or timezone.now()
    rule = rule_for(organization=organization, code="SENSOR_SILENT")
    silent_after = rule["threshold"].get("hours", 2)

    found = []
    for excursion in Excursion.objects.filter(
        organization=organization, ended_at__isnull=True
    ).select_related("sensor__location"):
        found.append(
            about(
                excursion,
                code="COLD_CHAIN_EXCURSION",
                severity=Severity.CRITICAL,
                title=f"{excursion.sensor.location.name} is out of range",
                detail=(
                    f"{excursion.peak_celsius}°C for {excursion.duration_minutes} "
                    f"minutes. {excursion.quarantined_base:,} units held."
                ),
                meta={
                    "peak_celsius": str(excursion.peak_celsius),
                    "minutes": excursion.duration_minutes,
                },
            )
        )

    for sensor in Sensor.objects.filter(
        organization=organization, is_active=True
    ).select_related("location"):
        if sensor.last_seen_at is None:
            continue
        hours = (now - sensor.last_seen_at).total_seconds() / 3600
        if hours >= silent_after:
            found.append(
                about(
                    sensor,
                    code="SENSOR_SILENT",
                    severity=Severity.WARNING,
                    title=f"{sensor.location.name} sensor has not reported",
                    detail=f"Last reading {int(hours)} hours ago.",
                    meta={"hours": int(hours)},
                )
            )
    return found
