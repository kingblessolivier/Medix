"""Global search.

One question — "where is that thing" — across products, batches, orders,
invoices, documents, patients and pharmacies. A pharmacist holding a
carton with a number on it should not have to know which screen that
number belongs to.

**Every query is tenant-scoped and typed.** There is no free-form field
lookup: each source names the columns it searches, so a query string can
never reach a column nobody meant to expose. Patient results are a
separate, capability-gated source for the same reason.

Results carry the screen that opens them, so the palette can navigate
rather than just report.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

from django.db.models import Q

from core.models import Organization, User

#: Beyond this per source the palette is a list nobody reads. Narrowing
#: the query is faster than scrolling.
PER_SOURCE = 5


@dataclass(frozen=True)
class Hit:
    kind: str
    id: str
    title: str
    subtitle: str
    #: Which screen opens it. Without this a result is a fact rather than
    #: a destination.
    screen: str

    def as_dict(self) -> dict:
        return asdict(self)


def _products(organization: Organization, term: str) -> list[Hit]:
    from catalog.models import Product

    rows = Product.objects.filter(
        Q(name__icontains=term)
        | Q(generic_name__icontains=term)
        | Q(brand__icontains=term)
        | Q(gtin=term),
        organization=organization,
    ).select_related("category")[:PER_SOURCE]

    return [
        Hit(
            kind="product",
            id=str(row.id),
            title=row.name,
            subtitle=row.generic_name or (row.category.name if row.category_id else ""),
            screen="catalogue",
        )
        for row in rows
    ]


def _batches(organization: Organization, term: str) -> list[Hit]:
    from inventory.models import Batch

    rows = (
        Batch.objects.filter(
            Q(batch_number__icontains=term) | Q(serial=term) | Q(gtin=term),
            organization=organization,
        )
        .select_related("product")
        .order_by("expiry_date")[:PER_SOURCE]
    )
    return [
        Hit(
            kind="batch",
            id=str(row.id),
            title=row.batch_number,
            subtitle=f"{row.product.name} · expires {row.expiry_date:%d %b %Y}",
            # Recall is where a batch number is most often typed in anger.
            screen="recall",
        )
        for row in rows
    ]


def _orders(organization: Organization, term: str) -> list[Hit]:
    from commerce.models import PurchaseOrder

    rows = (
        PurchaseOrder.objects.filter(
            Q(organization=organization) | Q(supplier=organization),
        )
        .filter(number__icontains=term)
        .select_related("supplier", "organization")[:PER_SOURCE]
    )
    return [
        Hit(
            kind="order",
            id=str(row.id),
            title=row.number,
            subtitle=(
                f"{row.organization.name} → {row.supplier.name} · "
                f"{row.get_status_display()}"
            ),
            # The buyer's screen and the depot's are different; the depot
            # sees its own incoming orders under distribution.
            screen="distribution" if row.supplier_id == organization.id else "orders",
        )
        for row in rows
    ]


def _invoices(organization: Organization, term: str) -> list[Hit]:
    from commerce.models import Invoice

    rows = (
        Invoice.objects.filter(number__icontains=term, organization=organization)
        .select_related("customer")[:PER_SOURCE]
    )
    return [
        Hit(
            kind="invoice",
            id=str(row.id),
            title=row.number,
            subtitle=f"{row.customer.name} · {row.get_status_display()}",
            screen="finance",
        )
        for row in rows
    ]


#: Where a document takes you. There is no document screen — a document
#: is an output of an operation and lives on that operation's row, so a
#: search hit navigates to the work rather than to a filing cabinet.
#: See docs/19-screens.md.
DOCUMENT_SCREENS = {
    "commerce.Shipment": "distribution",
    "commerce.PurchaseOrder": "orders",
    "commerce.Invoice": "finance",
    "commerce.GoodsReceipt": "receiving",
    "commerce.ControlledTransfer": "compliance",
    "finance.WriteOff": "finance",
    "insurance.Claim": "claims",
}


def _documents(organization: Organization, term: str) -> list[Hit]:
    from documents.models import Document

    rows = Document.objects.filter(
        number__icontains=term, organization=organization
    )[:PER_SOURCE]
    return [
        Hit(
            kind="document",
            id=str(row.id),
            title=row.number,
            subtitle=row.get_kind_display(),
            screen=DOCUMENT_SCREENS.get(row.subject_type, "orders"),
        )
        for row in rows
    ]


def _pharmacies(organization: Organization, term: str) -> list[Hit]:
    from commerce.models import TradingRelationship

    rows = (
        TradingRelationship.objects.filter(
            customer__name__icontains=term, organization=organization
        )
        .select_related("customer")[:PER_SOURCE]
    )
    return [
        Hit(
            kind="pharmacy",
            id=str(row.customer_id),
            title=row.customer.name,
            subtitle="Customer",
            screen="pharmacies",
        )
        for row in rows
    ]


def _patients(organization: Organization, term: str) -> list[Hit]:
    """Gated separately, and audited by the caller.

    Patient data is the most sensitive thing here, so it is not folded
    into the general sweep — a search that returns patients to anyone who
    can search at all is a search that leaks.
    """
    from sales.models import Patient

    rows = Patient.objects.filter(
        Q(full_name__icontains=term) | Q(phone__icontains=term),
        organization=organization,
    )[:PER_SOURCE]
    return [
        Hit(
            kind="patient",
            id=str(row.id),
            title=row.full_name,
            subtitle=row.phone or "No phone recorded",
            screen="prescriptions",
        )
        for row in rows
    ]


def search(*, user: User, term: str) -> dict:
    """Everything matching, grouped by kind.

    Two characters minimum: one letter matches most of a catalogue and
    the result is noise rather than an answer.
    """
    term = term.strip()
    organization = user.organization
    if organization is None or len(term) < 2:
        return {"term": term, "results": []}

    from core.capabilities import Capability, has_capability

    sources = [_products, _batches, _orders, _invoices, _documents, _pharmacies]
    # Dispensing capability is what makes patient records this user's
    # business at all.
    if has_capability(organization, Capability.DISPENSE):
        sources.append(_patients)

    hits: list[Hit] = []
    for source in sources:
        hits.extend(source(organization, term))

    return {"term": term, "results": [hit.as_dict() for hit in hits]}
