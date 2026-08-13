"""Draining the journal — and what happens when it cannot.

The behaviour worth defending is the difference between *offline* and
*failed*. A network outage must leave entries pending; only a payload the
server actually rejected is a failure. Confusing the two burns the retry
budget on the server being unreachable, which is precisely the condition
the agent exists to survive.
"""

import httpx
import pytest

from medix_agent.journal import Journal
from medix_agent.sync import BACKOFF, Credentials, SyncClient


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / "journal.sqlite3")


def client_with(journal, handler):
    """A SyncClient whose transport is a function, not a socket."""
    client = SyncClient(
        Credentials(
            base_url="http://testserver",
            device_code="TILL-1",
            username="agent",
            password="secret",
        ),
        journal,
    )
    client._client = httpx.Client(
        base_url="http://testserver", transport=httpx.MockTransport(handler)
    )
    return client


def responds(status=200, body=None, *, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return httpx.Response(status, json=body if body is not None else {})

    return handler


class TestDraining:
    def test_an_empty_journal_sends_nothing(self, journal):
        calls: list[httpx.Request] = []
        client = client_with(journal, responds(calls=calls))
        assert client.drain() == {"sent": 0, "failed": 0, "pending": 0}
        assert calls == []

    def test_an_applied_envelope_is_marked_sent(self, journal):
        entry = journal.record("sale", {"total": 5000})
        client = client_with(
            journal,
            responds(
                body={
                    "results": [
                        {
                            "client_id": entry.client_id,
                            "status": "APPLIED",
                            "result": {"number": "SALE-0001"},
                        }
                    ]
                }
            ),
        )
        assert client.drain()["sent"] == 1
        assert journal.pending() == []

    def test_a_duplicate_is_as_good_as_applied(self, journal):
        """The server already had it. Retrying blindly is the design."""
        entry = journal.record("sale", {})
        client = client_with(
            journal,
            responds(
                body={"results": [{"client_id": entry.client_id, "status": "DUPLICATE"}]}
            ),
        )
        assert client.drain()["sent"] == 1
        assert journal.counts() == {"sent": 1}

    def test_a_rejected_payload_is_marked_failed(self, journal):
        entry = journal.record("sale", {})
        client = client_with(
            journal,
            responds(
                body={
                    "results": [
                        {
                            "client_id": entry.client_id,
                            "status": "FAILED",
                            "error": "Batch not found",
                        }
                    ]
                }
            ),
        )
        assert client.drain()["failed"] == 1
        assert journal.pending()[0].attempts == 1

    def test_the_whole_batch_goes_in_one_request(self, journal):
        """A reconnection after hours offline must not be a stampede."""
        for _ in range(20):
            journal.record("sale", {})
        calls: list[httpx.Request] = []
        client = client_with(journal, responds(body={"results": []}, calls=calls))
        client.drain()

        assert len(calls) == 1

    def test_the_device_code_travels_with_the_batch(self, journal):
        import json

        journal.record("sale", {})
        calls: list[httpx.Request] = []
        client = client_with(journal, responds(body={"results": []}, calls=calls))
        client.drain()

        body = json.loads(calls[0].content)
        assert body["device"] == "TILL-1"
        assert len(body["envelopes"]) == 1


class TestOffline:
    def test_an_unreachable_server_leaves_everything_pending(self, journal):
        journal.record("sale", {})

        def unreachable(request):
            raise httpx.ConnectError("no route to host")

        outcome = client_with(journal, unreachable).drain()

        assert outcome["offline"]
        assert outcome["failed"] == 0
        assert len(journal.pending()) == 1

    def test_an_outage_does_not_count_as_an_attempt(self, journal):
        """Otherwise the retry budget is spent on the network being down."""
        journal.record("sale", {})

        def unreachable(request):
            raise httpx.ConnectError("no route to host")

        client = client_with(journal, unreachable)
        client.drain()
        client.drain()

        assert journal.pending()[0].attempts == 0

    def test_a_server_error_leaves_the_batch_pending(self, journal):
        journal.record("sale", {})
        outcome = client_with(journal, responds(503)).drain()

        assert outcome["pending"] == 1
        assert journal.pending()[0].attempts == 0

    def test_a_refused_batch_does_not_fail_its_payloads(self, journal):
        """A bad device code is a registration problem, not a bad sale."""
        journal.record("sale", {})
        outcome = client_with(journal, responds(400, {"detail": "Unknown device"})).drain()

        assert outcome["refused"]
        assert journal.counts() == {"pending": 1}


class TestAuthentication:
    def test_a_401_re_authenticates_once_and_retries(self, journal):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            if request.url.path == "/api/v1/auth/token/":
                return httpx.Response(200, json={"access": "fresh-token"})
            if len([p for p in seen if p == "/api/v1/sync/"]) == 1:
                return httpx.Response(401)
            return httpx.Response(200, json={"results": []})

        journal.record("sale", {})
        client_with(journal, handler).drain()

        assert seen == ["/api/v1/sync/", "/api/v1/auth/token/", "/api/v1/sync/"]

    def test_the_token_is_kept_across_restarts(self, journal):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/auth/token/":
                return httpx.Response(200, json={"access": "fresh-token"})
            return httpx.Response(401)

        journal.record("sale", {})
        client_with(journal, handler).drain()

        assert journal.get("access_token") == "fresh-token"

    def test_bad_credentials_are_not_retried_forever(self, journal):
        """Wrong password is not a network problem; another attempt won't fix it."""
        attempts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request.url.path)
            if request.url.path == "/api/v1/auth/token/":
                return httpx.Response(401, json={"detail": "No active account"})
            return httpx.Response(401)

        journal.record("sale", {})
        client_with(journal, handler).drain()

        assert attempts.count("/api/v1/auth/token/") == 1


class TestReadings:
    def test_readings_go_straight_out(self, journal):
        """Not through the journal — a late excursion is a write-off."""
        calls: list[httpx.Request] = []
        client = client_with(journal, responds(201, calls=calls))

        assert client.send_readings("FRIDGE-1", [{"celsius": 4.0, "at": "now"}])
        assert calls[0].url.path == "/api/v1/telemetry/"

    def test_a_failed_send_says_so_rather_than_pretending(self, journal):
        def unreachable(request):
            raise httpx.ConnectError("down")

        assert not client_with(journal, unreachable).send_readings("FRIDGE-1", [])


class TestBackoff:
    def test_it_is_capped(self):
        """A fleet must not hammer a server that is down."""
        assert BACKOFF[-1] == 300
        assert BACKOFF == sorted(BACKOFF)
