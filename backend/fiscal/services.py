"""Fiscal integration boundary.

The POS must never know what the tax authority's interface looks like
this year. Everything goes through `FiscalIntegrationService`, so a
change at RRA is a new adapter rather than a rewrite.

**Open question V1** — RRA's VSDC is distributed as a WAR file deployed on
the taxpayer's own local webserver. If that is mandatory, submission
cannot originate in the cloud and must go through the site agent. The
boundary below is deliberately transport-agnostic so that answer changes
one adapter and nothing else.

See docs/06-compliance.md §5 and docs/11-risks.md R1.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from fiscal.models import FiscalRecord, FiscalStatus
from sales.models import Sale, SaleStatus


@dataclass(frozen=True)
class FiscalOutcome:
    accepted: bool
    receipt_number: str = ""
    signature: str = ""
    device_id: str = ""
    payload: dict | None = None
    error_code: str = ""
    error_message: str = ""


class FiscalBackend(ABC):
    """One adapter per fiscal transport."""

    code: str

    @abstractmethod
    def submit(self, sale: Sale) -> FiscalOutcome:
        ...


class MockFiscalBackend(FiscalBackend):
    """Development stand-in. Never used outside local and CI."""

    code = "mock"

    def submit(self, sale: Sale) -> FiscalOutcome:
        return FiscalOutcome(
            accepted=True,
            receipt_number=f"MOCK-{uuid.uuid4().hex[:10].upper()}",
            signature=uuid.uuid4().hex,
            device_id="MOCK-DEVICE",
            payload={
                "total": sale.total,
                "tax": sale.tax_total,
                # Per-line treatment is what RRA needs; a pharmacy basket
                # is mixed, so a single total would be wrong.
                "lines": [
                    {
                        "product": line.product.name,
                        "quantity": line.quantity,
                        "amount": line.line_total,
                        "tax_treatment": line.tax_treatment,
                        "tax_amount": line.tax_amount,
                    }
                    for line in sale.lines.all()
                ],
            },
        )


class VsdcBackend(FiscalBackend):
    """RRA VSDC, reached through the site agent.

    Not implemented until V1 is answered: whether a cloud-hosted
    per-tenant VSDC is permissible, or whether it must be on-premise. The
    class exists so the boundary is real rather than hypothetical.
    """

    code = "vsdc"

    def submit(self, sale: Sale) -> FiscalOutcome:
        raise NotImplementedError(
            "VSDC submission is pending verification V1 — see docs/11-risks.md."
        )


BACKENDS: dict[str, type[FiscalBackend]] = {
    "mock": MockFiscalBackend,
    "vsdc": VsdcBackend,
}


def get_backend() -> FiscalBackend:
    code = getattr(settings, "FISCAL_BACKEND", "mock")
    try:
        return BACKENDS[code]()
    except KeyError:
        raise RuntimeError(f"Unknown fiscal backend {code!r}.")


class FiscalIntegrationService:
    """What the rest of the system calls. Nothing else touches a backend."""

    def __init__(self, backend: FiscalBackend | None = None):
        self.backend = backend or get_backend()

    @transaction.atomic
    def submit(self, sale: Sale) -> FiscalRecord:
        """Submit a sale, recording the outcome either way.

        Preserves: a completed sale always has a fiscal record. A failure
        lands in the exception queue rather than disappearing, because a
        sale nobody knows is unfiscalized is the one that becomes an RRA
        finding.
        """
        # Goods have left the counter, so the invoice is due now —
        # whether the money has settled is a separate question. Only a
        # sale that never happened is unfiscalizable.
        if sale.status in (SaleStatus.DRAFT, SaleStatus.VOIDED):
            raise RuntimeError("Only a posted sale is fiscalized.")

        record, _ = FiscalRecord.objects.get_or_create(
            sale=sale,
            defaults={"organization": sale.organization, "backend": self.backend.code},
        )
        if record.is_accepted:
            return record

        record.attempts += 1
        record.submitted_at = timezone.now()
        record.backend = self.backend.code

        try:
            outcome = self.backend.submit(sale)
        except Exception as exc:  # noqa: BLE001 — recorded, not swallowed
            record.status = FiscalStatus.FAILED
            record.error_code = type(exc).__name__
            record.error_message = str(exc)
            record.resolved_at = None
            record.save()
            return record

        if outcome.accepted:
            record.status = FiscalStatus.ACCEPTED
            record.receipt_number = outcome.receipt_number
            record.fiscal_signature = outcome.signature
            record.device_id = outcome.device_id
            record.payload = outcome.payload
            record.error_code = ""
            record.error_message = ""
            record.resolved_at = timezone.now()
        else:
            record.status = FiscalStatus.REJECTED
            record.error_code = outcome.error_code
            record.error_message = outcome.error_message
            record.resolved_at = timezone.now()

        record.save()
        return record

    def retry(self, record: FiscalRecord) -> FiscalRecord:
        if record.is_accepted:
            return record
        return self.submit(record.sale)


def exceptions_for(organization):
    """The queue an operator works through. A screen, not a log file."""
    return (
        FiscalRecord.objects.filter(
            organization=organization,
            status__in=[FiscalStatus.REJECTED, FiscalStatus.FAILED],
        )
        .select_related("sale")
        .order_by("-created_at")
    )


def submission_rate(organization, *, since) -> float:
    """Accepted as a share of all attempts. Monitored — see docs/17."""
    records = FiscalRecord.objects.filter(organization=organization, created_at__gte=since)
    total = records.count()
    if total == 0:
        return 1.0
    return records.filter(status=FiscalStatus.ACCEPTED).count() / total
