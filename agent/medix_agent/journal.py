"""The offline journal.

A point of sale that stops selling when the connection drops is
worthless, so everything the site does is written here first and
forwarded afterwards. SQLite because it is a single file on a machine
that may lose power mid-write, and its durability guarantees are the
whole reason to prefer it to a directory of JSON.

**Nothing is deleted on send.** An entry moves to `sent` with the
server's answer beside it. A pharmacy that has to prove what its till
recorded during a four-hour outage has the file, and a queue that erases
on success cannot answer that question.

See docs/02-architecture.md.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    client_id   TEXT PRIMARY KEY,
    sequence    INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'pending',
    attempts    INTEGER NOT NULL DEFAULT 0,
    result      TEXT,
    error       TEXT,
    sent_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_entries_state ON entries(state, sequence);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Entry:
    client_id: str
    sequence: int
    kind: str
    payload: dict
    occurred_at: str
    attempts: int = 0

    def as_envelope(self) -> dict:
        return {
            "client_id": self.client_id,
            "sequence": self.sequence,
            "kind": self.kind,
            "payload": self.payload,
            "occurred_at": self.occurred_at,
        }


class Journal:
    """Append-only locally, drained in order.

    Order is per device and deliberately not global: one till's sequence
    must replay in order, but waiting on another till's backlog would
    stop this one selling.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            # WAL survives an unclean shutdown far better, which is the
            # case that matters on a shop-floor machine.
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    # -- writing ----------------------------------------------------------

    def record(self, kind: str, payload: dict, *, occurred_at: datetime | None = None) -> Entry:
        """Journal one operation. Returns immediately; sending is separate.

        The client id is generated here, not by the server, because the
        whole point is that this works with no server reachable.
        """
        moment = (occurred_at or datetime.now(timezone.utc)).isoformat()
        entry = Entry(
            client_id=uuid.uuid4().hex,
            sequence=self._next_sequence(),
            kind=kind,
            payload=payload,
            occurred_at=moment,
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO entries (client_id, sequence, kind, payload, occurred_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    entry.client_id,
                    entry.sequence,
                    entry.kind,
                    json.dumps(payload),
                    entry.occurred_at,
                ),
            )
        return entry

    def _next_sequence(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next FROM entries"
            ).fetchone()
            return int(row["next"])

    # -- reading ----------------------------------------------------------

    def pending(self, *, limit: int = 100) -> list[Entry]:
        """The next batch to send, oldest first.

        Failed entries are included so a transient error retries, but
        they sort by sequence like everything else — a poisoned payload
        must not jump the queue on every pass.
        """
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM entries WHERE state IN ('pending', 'failed')"
                " ORDER BY sequence LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            Entry(
                client_id=row["client_id"],
                sequence=row["sequence"],
                kind=row["kind"],
                payload=json.loads(row["payload"]),
                occurred_at=row["occurred_at"],
                attempts=row["attempts"],
            )
            for row in rows
        ]

    def counts(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) AS n FROM entries GROUP BY state"
            ).fetchall()
        return {row["state"]: row["n"] for row in rows}

    # -- settling ---------------------------------------------------------

    def mark_sent(self, client_id: str, result: dict | None = None) -> None:
        """Kept, not deleted. The journal is the pharmacy's own record."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE entries SET state='sent', result=?, sent_at=?, error=''"
                " WHERE client_id=?",
                (json.dumps(result or {}), datetime.now(timezone.utc).isoformat(), client_id),
            )

    def mark_failed(self, client_id: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE entries SET state='failed', attempts=attempts+1, error=?"
                " WHERE client_id=?",
                (error[:1000], client_id),
            )

    # -- meta -------------------------------------------------------------

    def get(self, key: str, default: str = "") -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
