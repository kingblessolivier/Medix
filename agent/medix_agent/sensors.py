"""Reading the fridge.

Temperature is the one thing the agent sends immediately rather than
journalling: an excursion caught in ten minutes is a fridge somebody can
still save, and one caught at the next reconnection is a write-off. If
the direct send fails it falls back to the journal, so nothing is lost —
it just arrives late, and the server marks it as buffered so nobody
mistakes a late reading for one that was actionable at the time.

Two drivers ship. A file driver reads whatever a USB probe's own daemon
writes, which is how most cheap loggers work; a mock drives tests and
lets a site be commissioned before hardware arrives.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Sample:
    sensor_code: str
    celsius: float
    at: datetime

    def as_dict(self, *, buffered: bool = False) -> dict:
        return {
            "celsius": self.celsius,
            "at": self.at.isoformat(),
            "buffered": buffered,
        }


@runtime_checkable
class SensorDriver(Protocol):
    def read(self) -> list[Sample]: ...


class FileDriver:
    """Reads a JSON file a probe's own daemon maintains.

    Most inexpensive USB loggers ship a vendor daemon that writes a small
    status file rather than exposing a protocol. Reading that file is
    less clever than speaking to the device and considerably more likely
    to keep working when the vendor ships an update.

    Expected shape: `{"sensors": [{"code": "FRIDGE-1", "celsius": 4.2}]}`
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def read(self) -> list[Sample]:
        if not self.path.exists():
            log.warning("Sensor file %s is missing", self.path)
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # A half-written file is normal — the daemon may be mid-write.
            # Skipping one poll is correct; guessing a value is not.
            log.warning("Cannot read %s: %s", self.path, exc)
            return []

        now = datetime.now(timezone.utc)
        return [
            Sample(
                sensor_code=entry["code"],
                celsius=float(entry["celsius"]),
                at=now,
            )
            for entry in data.get("sensors", [])
            if entry.get("celsius") is not None
        ]


class MockDriver:
    """A fixed sequence, for tests and commissioning."""

    def __init__(self, samples: list[tuple[str, float]]) -> None:
        self._samples = samples

    def read(self) -> list[Sample]:
        now = datetime.now(timezone.utc)
        return [
            Sample(sensor_code=code, celsius=celsius, at=now)
            for code, celsius in self._samples
        ]


class Monitor:
    """Polls, sends, and falls back to the journal when offline."""

    def __init__(self, driver: SensorDriver, client, journal) -> None:
        self.driver = driver
        self.client = client
        self.journal = journal

    def poll_once(self) -> dict:
        samples = self.driver.read()
        if not samples:
            return {"read": 0, "sent": 0, "buffered": 0}

        by_sensor: dict[str, list[Sample]] = {}
        for sample in samples:
            by_sensor.setdefault(sample.sensor_code, []).append(sample)

        sent = 0
        buffered = 0
        for code, group in by_sensor.items():
            readings = [sample.as_dict() for sample in group]
            if self.client.send_readings(code, readings):
                sent += len(group)
                continue
            # Offline. Journalled instead, and flagged buffered so a late
            # reading is never mistaken for one that was actionable when
            # it was taken.
            self.journal.record(
                "temperature",
                {
                    "sensor": code,
                    "readings": [sample.as_dict(buffered=True) for sample in group],
                },
            )
            buffered += len(group)

        return {"read": len(samples), "sent": sent, "buffered": buffered}

    def run_forever(self, *, interval: int = 300) -> None:
        """Every five minutes by default.

        Frequent enough that a thirty-minute grace window sees several
        readings, infrequent enough not to fill the journal during a long
        outage.
        """
        while True:
            try:
                self.poll_once()
            except Exception:
                # A monitor that dies on one bad poll stops watching the
                # fridge, which is worse than any single bad reading.
                log.exception("Sensor poll failed")
            time.sleep(interval)
