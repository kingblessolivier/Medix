"""Polling the fridge, and the fallback when the line is down.

Two properties: a reading is never invented from a half-written file, and
a reading that could not be sent live is journalled *flagged as buffered*
— so nobody later mistakes a late reading for one that was actionable at
the time it was taken.
"""

import json
from datetime import datetime, timezone

import pytest

from medix_agent.journal import Journal
from medix_agent.sensors import FileDriver, MockDriver, Monitor, Sample


class FakeClient:
    """Records what was sent; can be told to be offline."""

    def __init__(self, *, online: bool = True) -> None:
        self.online = online
        self.sent: list[tuple[str, list[dict]]] = []

    def send_readings(self, sensor_code, readings):
        self.sent.append((sensor_code, readings))
        return self.online


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / "journal.sqlite3")


class TestFileDriver:
    def write(self, tmp_path, content):
        path = tmp_path / "sensors.json"
        path.write_text(content, encoding="utf-8")
        return path

    def test_it_reads_what_the_vendor_daemon_wrote(self, tmp_path):
        path = self.write(
            tmp_path,
            json.dumps({"sensors": [{"code": "FRIDGE-1", "celsius": 4.2}]}),
        )
        samples = FileDriver(path).read()

        assert [(s.sensor_code, s.celsius) for s in samples] == [("FRIDGE-1", 4.2)]

    def test_a_half_written_file_is_skipped_not_guessed(self, tmp_path):
        """The daemon may be mid-write. One missed poll beats one invention."""
        path = self.write(tmp_path, '{"sensors": [{"code": "FRIDG')
        assert FileDriver(path).read() == []

    def test_a_missing_file_reads_nothing(self, tmp_path):
        assert FileDriver(tmp_path / "absent.json").read() == []

    def test_a_sensor_with_no_reading_is_left_out(self, tmp_path):
        path = self.write(
            tmp_path,
            json.dumps(
                {
                    "sensors": [
                        {"code": "FRIDGE-1", "celsius": 4.2},
                        {"code": "FRIDGE-2", "celsius": None},
                    ]
                }
            ),
        )
        assert [s.sensor_code for s in FileDriver(path).read()] == ["FRIDGE-1"]

    def test_a_reading_is_stamped_when_it_was_taken(self, tmp_path):
        path = self.write(
            tmp_path, json.dumps({"sensors": [{"code": "FRIDGE-1", "celsius": 4.2}]})
        )
        sample = FileDriver(path).read()[0]
        assert sample.at.tzinfo is timezone.utc


class TestMonitor:
    def test_a_reading_goes_straight_out(self, journal):
        client = FakeClient()
        Monitor(MockDriver([("FRIDGE-1", 4.0)]), client, journal).poll_once()

        assert client.sent[0][0] == "FRIDGE-1"
        assert journal.counts() == {}

    def test_offline_it_falls_back_to_the_journal(self, journal):
        client = FakeClient(online=False)
        outcome = Monitor(MockDriver([("FRIDGE-1", 9.5)]), client, journal).poll_once()

        assert outcome == {"read": 1, "sent": 0, "buffered": 1}
        assert journal.counts() == {"pending": 1}

    def test_a_buffered_reading_is_flagged_as_such(self, journal):
        """A late reading was never actionable. It must not look like one."""
        Monitor(
            MockDriver([("FRIDGE-1", 9.5)]), FakeClient(online=False), journal
        ).poll_once()

        entry = journal.pending()[0]
        assert entry.kind == "temperature"
        assert entry.payload["readings"][0]["buffered"] is True

    def test_a_live_reading_is_not_flagged(self, journal):
        client = FakeClient()
        Monitor(MockDriver([("FRIDGE-1", 4.0)]), client, journal).poll_once()
        assert client.sent[0][1][0]["buffered"] is False

    def test_readings_are_grouped_by_sensor(self, journal):
        client = FakeClient()
        Monitor(
            MockDriver([("FRIDGE-1", 4.0), ("FRIDGE-2", 5.0), ("FRIDGE-1", 4.1)]),
            client,
            journal,
        ).poll_once()

        assert sorted(code for code, _ in client.sent) == ["FRIDGE-1", "FRIDGE-2"]
        by_code = dict(client.sent)
        assert len(by_code["FRIDGE-1"]) == 2

    def test_nothing_read_sends_nothing(self, journal):
        client = FakeClient()
        outcome = Monitor(MockDriver([]), client, journal).poll_once()

        assert outcome == {"read": 0, "sent": 0, "buffered": 0}
        assert client.sent == []

    def test_one_sensor_offline_does_not_lose_the_other(self, journal):
        """Partial failure buffers only what failed."""

        class Choosy(FakeClient):
            def send_readings(self, sensor_code, readings):
                self.sent.append((sensor_code, readings))
                return sensor_code == "FRIDGE-1"

        client = Choosy()
        outcome = Monitor(
            MockDriver([("FRIDGE-1", 4.0), ("FRIDGE-2", 5.0)]), client, journal
        ).poll_once()

        assert outcome == {"read": 2, "sent": 1, "buffered": 1}
        assert journal.pending()[0].payload["sensor"] == "FRIDGE-2"


class TestSample:
    def test_the_wire_shape_is_what_the_server_parses(self):
        sample = Sample("FRIDGE-1", 4.2, datetime(2026, 8, 12, 9, 0, tzinfo=timezone.utc))
        assert sample.as_dict() == {
            "celsius": 4.2,
            "at": "2026-08-12T09:00:00+00:00",
            "buffered": False,
        }
