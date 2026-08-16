"""Draining the journal to the cloud.

The agent retries blindly and that is deliberate: every envelope carries
a client-generated id and the server answers a repeat with the original
result rather than applying it twice. An agent that had to reason about
what got through is an agent that will get it wrong during the one outage
that matters.

Backoff is capped and jittered. Every pharmacy in the country reconnects
when the same network comes back, and a fleet retrying in lockstep is a
denial of service the operator inflicts on themselves.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass

import httpx

from medix_agent.journal import Journal

log = logging.getLogger(__name__)

#: Seconds. Caps at roughly five minutes — long enough not to hammer a
#: down server, short enough that a pharmacy back online is not waiting.
BACKOFF = [5, 15, 45, 120, 300]


@dataclass
class Credentials:
    base_url: str
    device_code: str
    username: str
    password: str
    agent_version: str = "0.1.0"


class SyncClient:
    """Talks to `/api/v1/sync/` and `/api/v1/telemetry/`."""

    def __init__(self, credentials: Credentials, journal: Journal) -> None:
        self.credentials = credentials
        self.journal = journal
        self._client = httpx.Client(base_url=credentials.base_url, timeout=30)
        self._access = journal.get("access_token")

    # -- auth -------------------------------------------------------------

    def _authenticate(self) -> bool:
        try:
            response = self._client.post(
                "/api/v1/auth/token/",
                json={
                    "username": self.credentials.username,
                    "password": self.credentials.password,
                },
            )
        except httpx.HTTPError as exc:
            log.warning("Cannot reach the server: %s", exc)
            return False

        if response.status_code != 200:
            # Credentials are wrong, not the network. Retrying will not
            # fix it, and saying so is more useful than another attempt.
            log.error("Authentication refused: %s", response.status_code)
            return False

        self._access = response.json()["access"]
        self.journal.set("access_token", self._access)
        return True

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access}"} if self._access else {}

    def _post(self, path: str, body: dict) -> httpx.Response | None:
        """One request, re-authenticating once on a 401."""
        try:
            response = self._client.post(path, json=body, headers=self._headers())
            if response.status_code == 401 and self._authenticate():
                response = self._client.post(path, json=body, headers=self._headers())
            return response
        except httpx.HTTPError as exc:
            log.warning("%s failed: %s", path, exc)
            return None

    # -- draining ---------------------------------------------------------

    def drain(self, *, batch_size: int = 100) -> dict:
        """Send everything pending, in one batch.

        Batched because the interesting case is a reconnection after
        hours offline, and one request per journalled sale would turn
        that into a stampede from every site at once.
        """
        entries = self.journal.pending(limit=batch_size)
        if not entries:
            return {"sent": 0, "failed": 0, "pending": 0}

        response = self._post(
            "/api/v1/sync/",
            {
                "device": self.credentials.device_code,
                "agent_version": self.credentials.agent_version,
                "envelopes": [entry.as_envelope() for entry in entries],
            },
        )
        if response is None:
            # Offline. Nothing is marked failed: the entries are simply
            # still pending, and counting a network outage as a failure
            # would burn the retry budget on the server being unreachable.
            return {"sent": 0, "failed": 0, "pending": len(entries), "offline": True}

        if response.status_code >= 500:
            log.warning("Server error %s; leaving the batch pending", response.status_code)
            return {"sent": 0, "failed": 0, "pending": len(entries)}

        if response.status_code >= 400:
            # The batch itself was rejected — a bad device code, usually.
            # Marking every entry failed would be wrong; the payloads are
            # fine and the registration is not.
            log.error("Sync refused: %s %s", response.status_code, response.text[:200])
            return {"sent": 0, "failed": 0, "pending": len(entries), "refused": True}

        sent = 0
        failed = 0
        for result in response.json().get("results", []):
            if result["status"] in ("APPLIED", "DUPLICATE"):
                self.journal.mark_sent(result["client_id"], result.get("result"))
                sent += 1
            else:
                self.journal.mark_failed(
                    result["client_id"], result.get("error", "Unknown error")
                )
                failed += 1

        return {"sent": sent, "failed": failed, "pending": len(entries) - sent - failed}

    def send_readings(self, sensor_code: str, readings: list[dict]) -> bool:
        """Temperature goes straight out rather than through the journal.

        An excursion caught in ten minutes is a fridge somebody can still
        save; one caught at the next reconnection is a write-off. If the
        direct send fails the caller journals it, so nothing is lost —
        it just arrives late.
        """
        response = self._post(
            "/api/v1/telemetry/", {"sensor": sensor_code, "readings": readings}
        )
        return response is not None and response.status_code < 300

    # -- loop -------------------------------------------------------------

    def run_forever(self, *, interval: int = 30) -> None:
        attempt = 0
        while True:
            outcome = self.drain()
            if outcome.get("offline") or outcome.get("refused"):
                delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
                # Jittered: every pharmacy reconnects when the same
                # network comes back, and a fleet retrying in lockstep is
                # a self-inflicted denial of service.
                time.sleep(delay + random.uniform(0, delay * 0.3))
                attempt += 1
                continue

            attempt = 0
            if outcome["pending"] == 0:
                time.sleep(interval)
