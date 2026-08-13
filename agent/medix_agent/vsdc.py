"""The VSDC bridge.

RRA's Virtual Sales Data Controller is distributed as a WAR file deployed
on the taxpayer's own local webserver, and each taxpayer applies for it
separately. A pure cloud service therefore **cannot** issue fiscal
invoices on a pharmacy's behalf — which is the first of the two reasons
this agent exists at all.

`docs/11` V1 is still open: whether a cloud-hosted per-tenant VSDC is
permissible. So this is written the same way the insurance module handles
V3 — both shapes behind one interface. If V1 comes back permissive the
cloud backend takes over and the agent keeps running for the second
reason, connectivity. Nothing here has to be rewritten either way.

**A fiscal signature is never invented.** Where the VSDC cannot be
reached the invoice is queued unsigned and marked as such. A receipt
printed with a fabricated signature is a fraud, not a fallback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FiscalResult:
    """What came back, or why nothing did.

    `signed` is the only field a receipt may act on. `pending` is a real
    state and not a soft failure — an invoice waiting for the VSDC is
    legitimate and must be resubmitted, not reprinted as though it were
    fine.
    """

    signed: bool
    pending: bool = False
    signature: str = ""
    receipt_number: str = ""
    device_serial: str = ""
    qr_content: str = ""
    error: str = ""


@runtime_checkable
class VsdcTransport(Protocol):
    """What a VSDC deployment must offer, wherever it lives."""

    def submit(self, invoice: dict) -> FiscalResult: ...


class LocalVsdc:
    """The WAR on this site's own network. The documented deployment."""

    def __init__(self, base_url: str, *, timeout: int = 20) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def submit(self, invoice: dict) -> FiscalResult:
        try:
            response = self._client.post("/vsdc/trnsSales/saveSales", json=invoice)
        except httpx.HTTPError as exc:
            # The till keeps selling. The invoice queues unsigned rather
            # than being printed with something invented.
            log.warning("VSDC unreachable: %s", exc)
            return FiscalResult(signed=False, pending=True, error=str(exc))

        if response.status_code >= 400:
            return FiscalResult(
                signed=False,
                pending=response.status_code >= 500,
                error=f"VSDC {response.status_code}: {response.text[:200]}",
            )

        body = response.json()
        data = body.get("data") or {}
        if body.get("resultCd") not in ("000", "0000"):
            # A business rejection, not a transport failure. Retrying
            # unchanged will be rejected identically.
            return FiscalResult(
                signed=False,
                pending=False,
                error=f"{body.get('resultCd')}: {body.get('resultMsg', '')}",
            )

        return FiscalResult(
            signed=True,
            signature=data.get("rcptSign", ""),
            receipt_number=str(data.get("rcptNo", "")),
            device_serial=data.get("sdcId", ""),
            qr_content=data.get("qrCodeUrl", ""),
        )


class HostedVsdc:
    """A per-tenant VSDC reached over the internet.

    Only usable if V1 comes back permissive. Present so the answer
    changes a setting rather than an architecture — and so the shape of
    the alternative is written down rather than imagined.
    """

    def __init__(self, base_url: str, token: str, *, timeout: int = 20) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._token = token

    def submit(self, invoice: dict) -> FiscalResult:
        try:
            response = self._client.post(
                "/vsdc/trnsSales/saveSales",
                json=invoice,
                headers={"Authorization": f"Bearer {self._token}"},
            )
        except httpx.HTTPError as exc:
            log.warning("Hosted VSDC unreachable: %s", exc)
            return FiscalResult(signed=False, pending=True, error=str(exc))

        if response.status_code >= 400:
            return FiscalResult(
                signed=False,
                pending=response.status_code >= 500,
                error=f"VSDC {response.status_code}",
            )
        data = response.json().get("data") or {}
        return FiscalResult(
            signed=True,
            signature=data.get("rcptSign", ""),
            receipt_number=str(data.get("rcptNo", "")),
            device_serial=data.get("sdcId", ""),
            qr_content=data.get("qrCodeUrl", ""),
        )


class UnavailableVsdc:
    """No VSDC configured. Every invoice queues.

    The honest default for a site that has not completed RRA activation
    yet: selling continues, nothing is signed, and the queue says exactly
    how much is waiting.
    """

    def submit(self, invoice: dict) -> FiscalResult:
        return FiscalResult(
            signed=False, pending=True, error="No VSDC configured on this site."
        )


def build(settings: dict) -> VsdcTransport:
    """Pick a transport from configuration."""
    mode = settings.get("mode", "none")
    if mode == "local":
        return LocalVsdc(settings["url"])
    if mode == "hosted":
        return HostedVsdc(settings["url"], settings["token"])
    return UnavailableVsdc()
