"""The fiscal bridge.

One rule dominates: **a signature is never invented**. Every test below
is ultimately a way of checking that where the VSDC did not sign, nothing
comes back claiming it did. A receipt printed with a fabricated signature
is a fraud, not a fallback.
"""

import httpx
import pytest

from medix_agent import vsdc

INVOICE = {"invcNo": 1, "totAmt": 5000}


def transport(handler, cls=vsdc.LocalVsdc, **kwargs):
    bridge = cls("http://localhost:8080", *kwargs.pop("args", ()), **kwargs)
    bridge._client = httpx.Client(
        base_url="http://localhost:8080", transport=httpx.MockTransport(handler)
    )
    return bridge


def responds(status=200, body=None):
    return lambda request: httpx.Response(status, json=body or {})


class TestLocalVsdc:
    def test_a_signed_invoice_carries_its_signature(self):
        bridge = transport(
            responds(
                body={
                    "resultCd": "000",
                    "data": {
                        "rcptSign": "ABCD1234EFGH5678",
                        "rcptNo": 42,
                        "sdcId": "SDC0010000123",
                        "qrCodeUrl": "https://myrra.rra.gov.rw/...",
                    },
                }
            )
        )
        result = bridge.submit(INVOICE)

        assert result.signed
        assert result.signature == "ABCD1234EFGH5678"
        assert result.receipt_number == "42"
        assert result.device_serial == "SDC0010000123"

    def test_an_unreachable_vsdc_queues_rather_than_signs(self):
        def down(request):
            raise httpx.ConnectError("connection refused")

        result = transport(down).submit(INVOICE)

        assert not result.signed
        assert result.pending
        assert result.signature == ""

    def test_a_business_rejection_is_not_pending(self):
        """Retrying an identical payload will be rejected identically."""
        result = transport(
            responds(body={"resultCd": "894", "resultMsg": "Invalid item code"})
        ).submit(INVOICE)

        assert not result.signed
        assert not result.pending
        assert "894" in result.error

    def test_a_server_fault_is_pending(self):
        result = transport(responds(502)).submit(INVOICE)
        assert not result.signed
        assert result.pending

    def test_a_client_error_is_not_pending(self):
        result = transport(responds(400)).submit(INVOICE)
        assert not result.signed
        assert not result.pending

    def test_four_zero_result_codes_also_count_as_success(self):
        result = transport(
            responds(body={"resultCd": "0000", "data": {"rcptSign": "X"}})
        ).submit(INVOICE)
        assert result.signed

    def test_a_missing_data_block_does_not_crash(self):
        result = transport(responds(body={"resultCd": "000"})).submit(INVOICE)
        assert result.signed
        assert result.signature == ""


class TestUnavailable:
    def test_everything_queues(self):
        result = vsdc.UnavailableVsdc().submit(INVOICE)
        assert not result.signed
        assert result.pending

    def test_it_says_why(self):
        assert "No VSDC configured" in vsdc.UnavailableVsdc().submit(INVOICE).error


class TestBuild:
    def test_no_configuration_is_the_honest_default(self):
        """A site that has not completed RRA activation still sells."""
        assert isinstance(vsdc.build({}), vsdc.UnavailableVsdc)

    def test_local_is_the_documented_deployment(self):
        assert isinstance(
            vsdc.build({"mode": "local", "url": "http://localhost:8080"}),
            vsdc.LocalVsdc,
        )

    def test_hosted_is_a_setting_not_an_architecture(self):
        """docs/11 V1 is open. Either answer changes one line of config."""
        bridge = vsdc.build(
            {"mode": "hosted", "url": "https://vsdc.example", "token": "t"}
        )
        assert isinstance(bridge, vsdc.HostedVsdc)

    def test_every_transport_satisfies_the_protocol(self):
        for bridge in (
            vsdc.UnavailableVsdc(),
            vsdc.LocalVsdc("http://localhost:8080"),
            vsdc.HostedVsdc("https://vsdc.example", "t"),
        ):
            assert isinstance(bridge, vsdc.VsdcTransport)


class TestTheRuleThatMatters:
    @pytest.mark.parametrize(
        "handler",
        [
            responds(500),
            responds(400),
            responds(body={"resultCd": "894", "resultMsg": "rejected"}),
        ],
    )
    def test_nothing_unsigned_ever_carries_a_signature(self, handler):
        result = transport(handler).submit(INVOICE)
        assert not result.signed
        assert result.signature == ""
        assert result.receipt_number == ""
