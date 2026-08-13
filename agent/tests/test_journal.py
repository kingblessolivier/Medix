"""The journal is the pharmacy's own record, not a send queue.

The distinction is the whole design: a queue erases on success, and a
pharmacy asked to prove what its till recorded during a four-hour outage
then has nothing. Everything here follows from keeping the entry.
"""

from datetime import datetime, timedelta, timezone

import pytest

from medix_agent.journal import Journal


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / "journal.sqlite3")


class TestRecording:
    def test_an_entry_is_pending_until_sent(self, journal):
        journal.record("sale", {"total": 5000})
        assert len(journal.pending()) == 1

    def test_the_client_id_is_generated_here(self, journal):
        """It must work with no server reachable. That is the point."""
        entry = journal.record("sale", {})
        assert entry.client_id

    def test_two_entries_never_share_an_id(self, journal):
        first = journal.record("sale", {})
        second = journal.record("sale", {})
        assert first.client_id != second.client_id

    def test_sequence_increases(self, journal):
        first = journal.record("sale", {})
        second = journal.record("sale", {})
        assert second.sequence == first.sequence + 1

    def test_the_payload_survives_a_round_trip(self, journal):
        journal.record("sale", {"lines": [{"quantity": 2, "uom_code": "PACK"}]})
        assert journal.pending()[0].payload["lines"][0]["uom_code"] == "PACK"

    def test_it_records_when_the_sale_happened(self, journal):
        """Not when it was sent. A sale belongs to the hour it was made."""
        when = datetime.now(timezone.utc) - timedelta(hours=5)
        entry = journal.record("sale", {}, occurred_at=when)
        assert entry.occurred_at == when.isoformat()


class TestDurability:
    def test_it_survives_the_process(self, tmp_path):
        path = tmp_path / "journal.sqlite3"
        Journal(path).record("sale", {"total": 5000})

        reopened = Journal(path)
        assert len(reopened.pending()) == 1

    def test_opening_an_existing_journal_does_not_wipe_it(self, tmp_path):
        path = tmp_path / "journal.sqlite3"
        Journal(path).record("sale", {})
        Journal(path)
        assert Journal(path).counts() == {"pending": 1}

    def test_it_creates_its_own_directory(self, tmp_path):
        journal = Journal(tmp_path / "nested" / "deeper" / "journal.sqlite3")
        journal.record("sale", {})
        assert journal.path.exists()


class TestSettling:
    def test_a_sent_entry_leaves_the_queue(self, journal):
        entry = journal.record("sale", {})
        journal.mark_sent(entry.client_id, {"number": "SALE-0001"})
        assert journal.pending() == []

    def test_a_sent_entry_is_kept(self, journal):
        """Nothing is deleted on send."""
        entry = journal.record("sale", {})
        journal.mark_sent(entry.client_id, {"number": "SALE-0001"})
        assert journal.counts() == {"sent": 1}

    def test_the_server_answer_is_kept_beside_it(self, journal):
        entry = journal.record("sale", {})
        journal.mark_sent(entry.client_id, {"number": "SALE-0001"})
        with journal._connect() as connection:
            row = connection.execute(
                "SELECT result, sent_at FROM entries WHERE client_id=?",
                (entry.client_id,),
            ).fetchone()
        assert "SALE-0001" in row["result"]
        assert row["sent_at"]

    def test_a_failed_entry_stays_in_the_queue(self, journal):
        entry = journal.record("sale", {})
        journal.mark_failed(entry.client_id, "Batch not found")
        assert [e.client_id for e in journal.pending()] == [entry.client_id]

    def test_failures_count_their_attempts(self, journal):
        entry = journal.record("sale", {})
        journal.mark_failed(entry.client_id, "boom")
        journal.mark_failed(entry.client_id, "boom")
        assert journal.pending()[0].attempts == 2

    def test_a_poisoned_entry_does_not_jump_the_queue(self, journal):
        """It retries in sequence, not ahead of everything behind it."""
        poisoned = journal.record("sale", {"bad": True})
        good = journal.record("sale", {})
        journal.mark_failed(poisoned.client_id, "boom")

        assert [e.client_id for e in journal.pending()] == [
            poisoned.client_id,
            good.client_id,
        ]

    def test_a_batch_is_capped(self, journal):
        for _ in range(10):
            journal.record("sale", {})
        assert len(journal.pending(limit=3)) == 3

    def test_pending_is_oldest_first(self, journal):
        entries = [journal.record("sale", {"n": n}) for n in range(5)]
        assert [e.client_id for e in journal.pending()] == [
            e.client_id for e in entries
        ]


class TestMeta:
    def test_a_missing_key_is_empty(self, journal):
        assert journal.get("access_token") == ""

    def test_a_token_survives_a_restart(self, tmp_path):
        path = tmp_path / "journal.sqlite3"
        Journal(path).set("access_token", "abc")
        assert Journal(path).get("access_token") == "abc"

    def test_setting_twice_replaces(self, journal):
        journal.set("access_token", "abc")
        journal.set("access_token", "def")
        assert journal.get("access_token") == "def"


class TestEnvelope:
    def test_the_envelope_carries_what_the_server_needs(self, journal):
        entry = journal.record("sale", {"total": 5000})
        envelope = entry.as_envelope()
        assert set(envelope) == {
            "client_id",
            "sequence",
            "kind",
            "payload",
            "occurred_at",
        }
