"""Payments.

Mobile money is **asynchronous**: request-to-pay, then the customer
confirms on their handset, then the provider calls back. `PENDING` is a
real state that may resolve in seconds, time out, or need manual
reconciliation — it is not a transient nicety.

The consequence for the counter: goods leave when the sale completes, but
the money may still be in flight. That is modelled honestly as
`PENDING_PAYMENT` rather than pretending the sale is settled.

Every provider sits behind `PaymentProvider`, so a provider change is a
new adapter and not a rewrite. See docs/02-architecture.md.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.exceptions import DomainError
from core.models import User
from sales.models import Payment, PaymentMethod, PaymentStatus, Sale, SaleStatus

#: How long a request-to-pay may stay unresolved before it is abandoned.
PENDING_TIMEOUT = timedelta(minutes=15)


class PaymentFailed(DomainError):
    default_code = "payment_failed"
    default_detail = "The payment did not go through."


class Overpayment(DomainError):
    default_code = "overpayment"
    default_detail = "That is more than the sale total."


@dataclass(frozen=True)
class ProviderRequest:
    """What a provider gives back when a payment is initiated."""

    reference: str
    #: True when the provider settled immediately (cash, card terminal).
    settled: bool


class PaymentProvider(ABC):
    """One adapter per provider. The POS never sees a provider's shape."""

    code: str

    @abstractmethod
    def request(self, *, amount: int, currency: str, phone: str, reference: str) -> ProviderRequest:
        ...


class CashProvider(PaymentProvider):
    code = "CASH"

    def request(self, *, amount, currency, phone, reference) -> ProviderRequest:
        # Cash is in the drawer the moment it is taken.
        return ProviderRequest(reference=reference, settled=True)


class MockMobileMoneyProvider(PaymentProvider):
    """Stands in for MTN MoMo and Airtel Money in development.

    Deliberately returns unsettled: the real providers always do, and a
    mock that settles instantly would hide every bug in the pending path.
    """

    code = "MOCK_MOMO"

    def request(self, *, amount, currency, phone, reference) -> ProviderRequest:
        return ProviderRequest(reference=f"MOCK-{uuid.uuid4().hex[:12]}", settled=False)


PROVIDERS: dict[str, PaymentProvider] = {
    "CASH": CashProvider(),
    "MOCK_MOMO": MockMobileMoneyProvider(),
}


def provider_for(method: str, provider_code: str = "") -> PaymentProvider:
    if method == PaymentMethod.CASH:
        return PROVIDERS["CASH"]
    code = provider_code or "MOCK_MOMO"
    try:
        return PROVIDERS[code]
    except KeyError:
        raise DomainError(f"Unknown payment provider {code}.", code="unknown_provider")


# --------------------------------------------------------------------------


def _sum(sale: Sale, status: str) -> int:
    """Query the database, never `sale.payments.all()`.

    A view that prefetches payments hands us a cached relation captured
    before the payment we just took. Reading it silently reports zero
    settled and leaves every paid sale sitting in PENDING_PAYMENT.
    """
    return (
        Payment.objects.filter(sale=sale, status=status).aggregate(total=Sum("amount"))[
            "total"
        ]
        or 0
    )


def amount_settled(sale: Sale) -> int:
    return _sum(sale, PaymentStatus.CONFIRMED)


def amount_pending(sale: Sale) -> int:
    return _sum(sale, PaymentStatus.PENDING)


def amount_outstanding(sale: Sale) -> int:
    return sale.total - amount_settled(sale) - amount_pending(sale)


@transaction.atomic
def take_payment(
    *,
    sale: Sale,
    method: str,
    amount: int,
    performed_by: User,
    provider_code: str = "",
    phone: str = "",
) -> Payment:
    """Record a payment against a sale.

    Cash settles immediately. Mobile money enters PENDING and resolves on
    the provider's callback.
    """
    if amount <= 0:
        raise DomainError("Amount must be positive.", code="invalid_amount")
    if sale.status in (SaleStatus.VOIDED,):
        raise DomainError("This sale was voided.", code="sale_voided")

    if amount > amount_outstanding(sale):
        raise Overpayment(
            f"Outstanding is {amount_outstanding(sale)}, offered {amount}.",
            meta={"outstanding": amount_outstanding(sale), "offered": amount},
        )

    provider = provider_for(method, provider_code)
    result = provider.request(
        amount=amount, currency=sale.currency, phone=phone, reference=str(sale.id)
    )

    payment = Payment.objects.create(
        organization=sale.organization,
        sale=sale,
        method=method,
        provider=provider.code if method != PaymentMethod.CASH else "",
        amount=amount,
        currency=sale.currency,
        status=PaymentStatus.CONFIRMED if result.settled else PaymentStatus.PENDING,
        provider_reference=result.reference,
        resolved_at=timezone.now() if result.settled else None,
        created_by=performed_by,
    )
    _refresh_sale_status(sale)
    return payment


@transaction.atomic
def resolve_payment(
    *, payment: Payment, confirmed: bool, provider_reference: str = ""
) -> Payment:
    """Apply a provider callback.

    Idempotent: a provider that retries its callback — which they do —
    must not flip a settled payment or double-count it.
    """
    if payment.status != PaymentStatus.PENDING:
        return payment

    payment.status = PaymentStatus.CONFIRMED if confirmed else PaymentStatus.FAILED
    payment.resolved_at = timezone.now()
    if provider_reference:
        payment.provider_reference = provider_reference
    payment.save(update_fields=["status", "resolved_at", "provider_reference", "modified_at"])

    _refresh_sale_status(payment.sale)
    return payment


@transaction.atomic
def expire_stale_payments(*, organization, now=None) -> int:
    """Time out request-to-pay attempts the customer never confirmed.

    Without this a sale sits in PENDING_PAYMENT for ever and the day never
    reconciles.
    """
    now = now or timezone.now()
    stale = Payment.objects.select_for_update().filter(
        organization=organization,
        status=PaymentStatus.PENDING,
        requested_at__lt=now - PENDING_TIMEOUT,
    )
    sales = set()
    count = 0
    for payment in stale:
        payment.status = PaymentStatus.TIMED_OUT
        payment.resolved_at = now
        payment.save(update_fields=["status", "resolved_at", "modified_at"])
        sales.add(payment.sale_id)
        count += 1

    for sale in Sale.objects.filter(id__in=sales):
        _refresh_sale_status(sale)
    return count


def _refresh_sale_status(sale: Sale) -> None:
    """A sale is COMPLETED only when the money is actually settled.

    Goods may already have left the counter — that is why PENDING_PAYMENT
    exists rather than calling an unsettled sale complete.
    """
    if sale.status in (SaleStatus.DRAFT, SaleStatus.VOIDED):
        return

    settled = amount_settled(sale)
    pending = amount_pending(sale)

    if settled >= sale.total:
        new_status = SaleStatus.COMPLETED
    elif pending > 0:
        new_status = SaleStatus.PENDING_PAYMENT
    else:
        # Nothing settled and nothing in flight — the sale is short-paid
        # and needs the counter to take payment again.
        new_status = SaleStatus.PENDING_PAYMENT

    if new_status != sale.status:
        sale.status = new_status
        if new_status == SaleStatus.COMPLETED and sale.completed_at is None:
            sale.completed_at = timezone.now()
        sale.save(update_fields=["status", "completed_at", "modified_at"])
