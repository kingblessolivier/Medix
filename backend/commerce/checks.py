"""Commercial and compliance alerts.

Credit was already enforced by `invoicing.assert_within_credit`; this
moves it under one framework so a hard block and a soft warning are the
same mechanism at two severities rather than two unrelated code paths.
"""

from __future__ import annotations

from datetime import date

from django.utils import timezone

from core.alerts import Alert, Severity, about, blocks_with, rule_for
from core.models import Organization


def credit_position(*, supplier: Organization, customer: Organization, pending: int = 0):
    from commerce import invoicing

    return invoicing.credit_position(
        supplier=supplier, customer=customer, pending=pending
    )


def _register() -> None:
    """Keep the named exceptions callers already catch.

    Moving credit under the alert framework must not silently change what
    a caller can catch. The wire `code` is identical either way; this
    keeps the Python class identical too.
    """
    from commerce.invoicing import CreditLimitExceeded
    from core.exceptions import LicenceInvalid

    blocks_with("CREDIT_LIMIT_EXCEEDED", CreditLimitExceeded)
    blocks_with("BUYER_LICENCE_EXPIRED", LicenceInvalid)


_register()


def credit(
    *, supplier: Organization, customer: Organization, pending: int = 0
) -> list[Alert]:
    """Blocked at the limit, warned approaching it.

    `pending` is the order being approved. Checking only historic debt
    lets a pharmacy sitting at its limit place one more order every time,
    because the new one is never part of the sum.
    """
    position = credit_position(supplier=supplier, customer=customer, pending=pending)
    limit = position["limit"]
    if limit <= 0:
        # No limit recorded means trading on immediate payment, not
        # unlimited credit. The check does not apply.
        return []

    # `credit_position` already folds `pending` into `outstanding`, so
    # this is the full exposure. Adding it again would count the order
    # under approval twice and refuse at half the real limit.
    exposure = position["outstanding"]
    if exposure > limit:
        return [
            about(
                customer,
                code="CREDIT_LIMIT_EXCEEDED",
                severity=rule_for(
                    organization=supplier, code="CREDIT_LIMIT_EXCEEDED"
                )["severity"],
                title=f"{customer.name} is over its credit limit",
                detail=f"{exposure:,} against a limit of {limit:,} RWF.",
                meta={"outstanding": position["outstanding"], "limit": limit},
            )
        ]

    rule = rule_for(organization=supplier, code="CREDIT_LIMIT_NEAR")
    percent = rule["threshold"].get("percent", 80)
    # Integer arithmetic: a float percentage of a money amount is exactly
    # the sort of rounding that makes a threshold fire a franc early.
    if exposure * 100 >= limit * percent:
        return [
            about(
                customer,
                code="CREDIT_LIMIT_NEAR",
                severity=rule["severity"],
                title=f"{customer.name} is near its credit limit",
                detail=f"{exposure * 100 // limit}% of {limit:,} RWF used.",
                meta={"outstanding": position["outstanding"], "limit": limit},
            )
        ]
    return []


def buyer_licence(*, customer: Organization, as_of: date | None = None) -> list[Alert]:
    """A supplier must not supply a pharmacy whose licence has lapsed.

    Critical, and checked on the depot's side: the buyer's own system
    already refuses them capability, but a supplier is separately
    responsible for who it ships to.
    """
    as_of = as_of or timezone.localdate()
    from core.models import LicenceKind

    holds_any = any(
        customer.holds(kind, at=as_of)
        for kind in (
            LicenceKind.RETAIL_PHARMACY,
            LicenceKind.WHOLESALE_PHARMACY,
            LicenceKind.IMPORTER,
            LicenceKind.DISTRIBUTOR,
        )
    )
    if holds_any:
        return []
    return [
        about(
            customer,
            code="BUYER_LICENCE_EXPIRED",
            severity=Severity.CRITICAL,
            title=f"{customer.name} holds no current licence",
            detail="Supply is not permitted until the licence is renewed.",
        )
    ]


def allocation_exhausted(*, listing) -> list[Alert]:
    """The depot has published more demand than it can meet."""
    if listing.available_base > 0:
        return []
    return [
        about(
            listing,
            code="ALLOCATION_EXHAUSTED",
            severity=rule_for(
                organization=listing.organization, code="ALLOCATION_EXHAUSTED"
            )["severity"],
            title=f"{listing.product.name} allocation is spoken for",
            detail=f"{listing.offered_base:,} offered, all committed.",
            meta={"offered_base": listing.offered_base},
        )
    ]


def receivables_overdue(*, supplier: Organization, as_of: date | None = None) -> list[Alert]:
    """Invoices past their terms, one alert per customer.

    Per customer rather than per invoice: a pharmacy with nine overdue
    invoices is one conversation, and nine banners is the fatigue this
    framework exists to avoid.
    """
    from commerce.models import Invoice, InvoiceKind, InvoiceStatus

    as_of = as_of or timezone.localdate()
    rule = rule_for(organization=supplier, code="RECEIVABLE_OVERDUE", as_of=as_of)
    threshold = rule["threshold"].get("days", 30)

    overdue: dict = {}
    invoices = Invoice.objects.filter(
        organization=supplier,
        kind=InvoiceKind.TAX,
        status__in=[InvoiceStatus.ISSUED, InvoiceStatus.PART_PAID],
        due_on__lt=as_of,
    ).select_related("customer")
    for invoice in invoices:
        days = invoice.days_overdue(as_of=as_of)
        if days < threshold or invoice.outstanding <= 0:
            continue
        entry = overdue.setdefault(
            invoice.customer_id,
            {"customer": invoice.customer, "amount": 0, "count": 0, "worst": 0},
        )
        entry["amount"] += invoice.outstanding
        entry["count"] += 1
        entry["worst"] = max(entry["worst"], days)

    return [
        about(
            entry["customer"],
            code="RECEIVABLE_OVERDUE",
            severity=rule["severity"],
            title=f"{entry['customer'].name} is {entry['worst']} days overdue",
            detail=f"{entry['amount']:,} RWF across {entry['count']} invoices.",
            meta={"amount": entry["amount"], "days": entry["worst"]},
        )
        for entry in sorted(overdue.values(), key=lambda e: -e["worst"])
    ]


def bulk_discount_available(*, listing, quantity_base: int) -> list[Alert]:
    """The buyer is close to a volume break they have not reached.

    Info, never a warning. It costs the buyer nothing to ignore, and a
    soft stop on "you could spend more" would be the system selling
    rather than informing.

    Only the next tier up is mentioned. Listing all of them turns a
    useful nudge into a price list.
    """
    in_price_uom = quantity_base // listing.price_uom.factor_to_base
    upcoming = [
        tier for tier in listing.tiers.all() if tier.min_quantity > in_price_uom
    ]
    if not upcoming:
        return []

    nearest = min(upcoming, key=lambda tier: tier.min_quantity)
    short_by = nearest.min_quantity - in_price_uom
    unit = listing.price_uom.code.lower()
    return [
        about(
            listing,
            code="BULK_DISCOUNT_AVAILABLE",
            severity=Severity.INFO,
            title=f"{short_by} more {unit} reaches a lower price",
            detail=f"{nearest.min_quantity}+ {unit} at {nearest.price:,} each.",
            meta={
                "short_by": short_by,
                "min_quantity": nearest.min_quantity,
                "price": nearest.price,
            },
        )
    ]


def below_cost(*, product, price_base: int, batch) -> list[Alert]:
    """About to sell below what this batch actually cost.

    Exact, not an average. FEFO records which batch is leaving and the
    cost lives on that batch, so there is no need to guess at a moving
    average that would be wrong for both the cheap batch and the dear one.
    """
    if batch is None or batch.unit_cost_base <= 0:
        return []
    if price_base >= batch.unit_cost_base:
        return []
    return [
        about(
            batch,
            code="SALE_BELOW_COST",
            severity=rule_for(
                organization=batch.organization, code="SALE_BELOW_COST"
            )["severity"],
            title=f"{product.name} priced below cost",
            detail=f"Batch {batch.batch_number} cost {batch.unit_cost_base:,} per unit.",
            meta={"price_base": price_base, "cost_base": batch.unit_cost_base},
        )
    ]
