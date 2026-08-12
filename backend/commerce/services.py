"""Commerce rules: listings, orders, receiving.

`post_receipt()` is where batches enter the system and where stock first
moves. Until it is called nothing has happened, which is what makes a
draft receipt safe to correct at the door.

See docs/05-modules.md §3–§5.
"""

from __future__ import annotations

import hashlib
from datetime import date

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from catalog.models import Product, UnitOfMeasure
from core import sequences
from core.capabilities import Capability, require_capability
from core.exceptions import DomainError, InsufficientStock
from core.models import Organization, User
from core.quantity import Quantity, from_base
from commerce.models import (
    Availability,
    GoodsReceipt,
    GoodsReceiptLine,
    GoodsReceiptStatus,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    Shipment,
    ShipmentLine,
    ShipmentStatus,
    TradingRelationship,
    VendorListing,
)
from inventory import services as inventory
from inventory.models import Batch, Location, MovementKind, StockStatus


class NotOrderable(DomainError):
    default_code = "not_orderable"
    default_detail = "This listing is not available to order."


class BelowMinimum(DomainError):
    default_code = "below_moq"
    default_detail = "That is below the supplier's minimum order."


class AlreadyPosted(DomainError):
    default_code = "already_posted"
    default_detail = "This receipt has already been posted."


class CustomerNotVerified(DomainError):
    default_code = "customer_not_verified"
    default_detail = "This customer has not been verified."


# --------------------------------------------------------------------------
# Listings
# --------------------------------------------------------------------------


@transaction.atomic
def publish_listing(
    *,
    organization: Organization,
    product: Product,
    price: int,
    price_uom: UnitOfMeasure,
    availability: str = Availability.AVAILABLE_NOW,
    moq: int = 1,
    lead_time_days: int = 1,
    performed_by: User | None = None,
) -> VendorListing:
    """Offer a product for sale.

    Only a wholesale pharmacy or importer may publish. A retail pharmacy
    that acquires a wholesale licence gains this without any code change,
    and loses it again when that licence lapses.
    """
    require_capability(organization, Capability.PUBLISH_LISTINGS)

    listing, _ = VendorListing.objects.update_or_create(
        organization=organization,
        product=product,
        defaults={
            "price": price,
            "price_uom": price_uom,
            "availability": availability,
            "moq": moq,
            "lead_time_days": lead_time_days,
            "is_active": True,
            "modified_by": performed_by,
        },
    )
    return listing


def compare_vendors(*, product: Product, as_of: date | None = None):
    """Every vendor offering a product, cheapest first.

    The system makes the tradeoff visible — price against expiry, MOQ and
    lead time — and does not choose. The cheapest listing is frequently
    the wrong one.
    """
    as_of = as_of or timezone.localdate()
    listings = (
        VendorListing.objects.filter(product=product, is_active=True)
        .select_related("organization", "price_uom")
        .order_by("price")
    )

    rows = []
    for listing in listings:
        stock = (
            inventory.balance_for(
                organization=listing.organization, product=product
            )
            if listing.availability == Availability.AVAILABLE_NOW
            else 0
        )
        earliest = (
            Batch.objects.filter(
                organization=listing.organization, product=product, expiry_date__gt=as_of
            )
            .order_by("expiry_date")
            .values_list("expiry_date", flat=True)
            .first()
        )
        rows.append(
            {
                "listing": listing,
                "vendor": listing.organization,
                "price": listing.price,
                "uom": listing.price_uom.code,
                "availability": listing.availability,
                "stock_base": stock,
                "earliest_expiry": earliest,
                "moq": listing.moq,
                "lead_time_days": listing.lead_time_days,
            }
        )
    return rows


# --------------------------------------------------------------------------
# Purchase orders
# --------------------------------------------------------------------------


@transaction.atomic
def start_order(
    *,
    organization: Organization,
    supplier: Organization,
    deliver_to: Location,
    performed_by: User,
    required_by: date | None = None,
) -> PurchaseOrder:
    return PurchaseOrder.objects.create(
        organization=organization,
        supplier=supplier,
        deliver_to=deliver_to,
        required_by=required_by,
        created_by=performed_by,
    )


def open_draft(
    *,
    organization: Organization,
    supplier: Organization,
    deliver_to: Location,
    performed_by: User,
) -> PurchaseOrder:
    """The draft this pharmacy is currently building for one supplier.

    Adding a product from the marketplace must not open a new order per
    click. One draft per supplier and delivery point is the unit a buyer
    thinks in: everything ordered from that vendor this morning is one
    purchase order, and the vendor gets one document to pick.
    """
    existing = (
        PurchaseOrder.objects.filter(
            organization=organization,
            supplier=supplier,
            deliver_to=deliver_to,
            status=PurchaseOrderStatus.DRAFT,
        )
        .order_by("-created_at")
        .first()
    )
    if existing is not None:
        return existing
    return start_order(
        organization=organization,
        supplier=supplier,
        deliver_to=deliver_to,
        performed_by=performed_by,
    )


@transaction.atomic
def add_order_line(
    *, order: PurchaseOrder, listing: VendorListing, quantity: int
) -> PurchaseOrderLine:
    if order.status != PurchaseOrderStatus.DRAFT:
        raise DomainError("This order has been submitted.", code="order_not_draft")
    if listing.organization_id != order.supplier_id:
        raise DomainError(
            "That listing belongs to a different supplier.", code="listing_supplier_mismatch"
        )
    if not listing.is_orderable:
        raise NotOrderable(
            f"{listing.product.name} is {listing.get_availability_display().lower()}. "
            "Raise an import request instead.",
            meta={"availability": listing.availability},
        )
    # Adding the same product twice means "more of it", not a second line
    # the vendor has to reconcile. Price is part of the match: if the
    # listing has repriced since the first add, that is a distinct line.
    line = order.lines.filter(
        product=listing.product, uom=listing.price_uom, unit_price=listing.price
    ).first()

    # The minimum applies to the line the supplier ends up picking, not to
    # each click. Topping a line of 10 up by 5 leaves 15, which clears a
    # minimum of 10 — refusing that would be arithmetic, not policy.
    resulting = (line.quantity if line is not None else 0) + quantity
    if resulting < listing.moq:
        raise BelowMinimum(
            f"Minimum order is {listing.moq} {listing.price_uom.code.lower()}.",
            meta={"moq": listing.moq, "requested": resulting},
        )

    if line is not None:
        line.quantity = resulting
        line.quantity_base = Quantity(resulting, listing.price_uom).base_value
        line.line_total = listing.price * resulting
        line.save(update_fields=["quantity", "quantity_base", "line_total"])
    else:
        line = PurchaseOrderLine.objects.create(
            order=order,
            product=listing.product,
            uom=listing.price_uom,
            quantity=quantity,
            quantity_base=Quantity(quantity, listing.price_uom).base_value,
            unit_price=listing.price,
            line_total=listing.price * quantity,
        )
    _recalculate_order(order)
    return line


def _recalculate_order(order: PurchaseOrder) -> None:
    order.subtotal = order.lines.aggregate(total=Sum("line_total"))["total"] or 0
    order.save(update_fields=["subtotal", "modified_at"])


@transaction.atomic
def submit_order(*, order: PurchaseOrder, performed_by: User) -> PurchaseOrder:
    """Send to the supplier. The supplier must have verified the buyer."""
    if order.status != PurchaseOrderStatus.DRAFT:
        raise DomainError("This order has been submitted.", code="order_not_draft")
    if not order.lines.exists():
        raise DomainError("Add a product before submitting.", code="order_empty")

    relationship = TradingRelationship.objects.filter(
        organization=order.supplier, customer=order.organization, is_active=True
    ).first()
    if relationship is None or not relationship.is_verified:
        raise CustomerNotVerified(
            f"{order.supplier.name} has not verified this pharmacy.",
            meta={"supplier_id": str(order.supplier_id)},
        )

    order.number = sequences.next_number(order.organization, "PURCHASE_ORDER")
    order.status = PurchaseOrderStatus.SUBMITTED
    order.submitted_at = timezone.now()
    order.modified_by = performed_by
    order.save(update_fields=["number", "status", "submitted_at", "modified_by", "modified_at"])
    return order


@transaction.atomic
def confirm_order(*, order: PurchaseOrder, performed_by: User) -> PurchaseOrder:
    """The supplier accepts. Only the supplier may do this."""
    if order.supplier_id != performed_by.organization_id:
        raise DomainError("Only the supplier can confirm an order.", code="not_supplier")
    if order.status != PurchaseOrderStatus.SUBMITTED:
        raise DomainError("This order is not awaiting confirmation.", code="order_not_submitted")

    order.status = PurchaseOrderStatus.CONFIRMED
    order.confirmed_at = timezone.now()
    order.modified_by = performed_by
    order.save(update_fields=["status", "confirmed_at", "modified_by", "modified_at"])
    return order


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------


def _dispatch_key(shipment_id, line_id) -> str:
    digest = hashlib.blake2s(f"{shipment_id}:{line_id}".encode(), digest_size=16).hexdigest()
    return f"dispatch:{digest}"


class NothingToDispatch(DomainError):
    default_code = "nothing_to_dispatch"
    default_detail = "Every line on this order has already been shipped."


@transaction.atomic
def dispatch_order(
    *,
    order: PurchaseOrder,
    from_location: Location,
    performed_by: User,
    carrier: str = "",
) -> Shipment:
    """Ship what is outstanding, and take it out of the supplier's ledger.

    This is the half that makes cross-organization trade honest. Receiving
    alone credits the buyer with stock that never left the seller, so the
    same cartons exist twice on the platform and every marketplace stock
    figure overstates.

    Picking is FEFO, so one order line can span several batches; each
    batch becomes its own delivery-note line. Those batch numbers and
    expiries are what the receiving pharmacy checks the cartons against,
    which is also why the buyer no longer has to type them from scratch.

    Short picks are allowed and are not an error: the supplier ships what
    they hold, the order stays partially dispatched, and the shortfall is
    still owed.
    """
    if order.supplier_id != performed_by.organization_id:
        raise DomainError("Only the supplier can dispatch an order.", code="not_supplier")
    if order.status not in (
        PurchaseOrderStatus.CONFIRMED,
        PurchaseOrderStatus.PARTIALLY_DISPATCHED,
        # Fully dispatched is allowed through so the outstanding check
        # below gives the precise reason — "already shipped" tells the
        # picker what happened; "not confirmed" would mislead them.
        PurchaseOrderStatus.DISPATCHED,
    ):
        raise DomainError(
            "Only a confirmed order can be dispatched.", code="order_not_confirmed"
        )
    if from_location.organization_id != order.supplier_id:
        raise DomainError("That location belongs to another organization.", code="wrong_location")

    outstanding = [line for line in order.lines.select_related("product", "uom") if line.undispatched_base > 0]
    if not outstanding:
        raise NothingToDispatch()

    shipment = Shipment.objects.create(
        organization=order.supplier,
        order=order,
        from_location=from_location,
        carrier=carrier,
        created_by=performed_by,
    )

    shipped_anything = False
    for line in outstanding:
        available = inventory.balance_for(
            organization=order.supplier, product=line.product, location=from_location
        )
        take = min(line.undispatched_base, available)
        if take <= 0:
            continue

        results = inventory.issue_fefo(
            organization=order.supplier,
            product=line.product,
            location=from_location,
            quantity=from_base(take, line.product.base_uom),
            kind=MovementKind.WHOLESALE_DISPATCH,
            performed_by=performed_by,
            reference=order.number,
            # Re-posting a dispatch must not issue the goods twice. Two
            # raw UUIDs overflow the 64-char key column, so the pair is
            # hashed — deterministic, and short enough to store.
            idempotency_key=_dispatch_key(shipment.id, line.id),
        )

        for result in results:
            movement = result.movement
            ShipmentLine.objects.create(
                shipment=shipment,
                order_line=line,
                product=line.product,
                uom=line.uom,
                quantity_base=abs(movement.quantity_base),
                batch=movement.batch,
                batch_number=movement.batch.batch_number,
                expiry_date=movement.batch.expiry_date,
            )

        line.dispatched_base += take
        line.save(update_fields=["dispatched_base"])
        shipped_anything = True

    if not shipped_anything:
        raise InsufficientStock(
            "No stock on hand for any line on this order.",
            code="no_stock_to_dispatch",
        )

    shipment.number = sequences.next_number(order.supplier, "DELIVERY_NOTE")
    shipment.status = ShipmentStatus.DISPATCHED
    shipment.dispatched_at = timezone.now()
    shipment.save(update_fields=["number", "status", "dispatched_at", "modified_at"])

    _update_dispatch_progress(order)
    return shipment


def _update_dispatch_progress(order: PurchaseOrder) -> None:
    # Query the lines rather than reading order.lines.all(): the viewset
    # prefetches that relation, so the cached rows still carry the
    # pre-dispatch tallies and a fully shipped order would be left
    # PARTIALLY_DISPATCHED forever.
    lines = list(PurchaseOrderLine.objects.filter(order=order))
    if all(line.undispatched_base == 0 for line in lines):
        order.status = PurchaseOrderStatus.DISPATCHED
    else:
        order.status = PurchaseOrderStatus.PARTIALLY_DISPATCHED
    order.save(update_fields=["status", "modified_at"])


# --------------------------------------------------------------------------
# Receiving
# --------------------------------------------------------------------------


@transaction.atomic
def start_receipt(
    *,
    organization: Organization,
    location: Location,
    performed_by: User,
    order: PurchaseOrder | None = None,
    supplier: Organization | None = None,
) -> GoodsReceipt:
    return GoodsReceipt.objects.create(
        organization=organization,
        location=location,
        order=order,
        supplier=supplier or (order.supplier if order else None),
        created_by=performed_by,
    )


@transaction.atomic
def add_receipt_line(
    *,
    receipt: GoodsReceipt,
    product: Product,
    uom: UnitOfMeasure,
    received: int,
    batch_number: str,
    expiry_date: date,
    accepted: int | None = None,
    rejected: int = 0,
    rejection_reason: str = "",
    unit_cost_base: int = 0,
    order_line: PurchaseOrderLine | None = None,
    gtin: str = "",
    serial: str = "",
) -> GoodsReceiptLine:
    """Record what actually arrived.

    Ordered, received, accepted and rejected are four separate numbers.
    A shortfall is a fact about the delivery, not something to correct
    away.
    """
    if receipt.status != GoodsReceiptStatus.DRAFT:
        raise AlreadyPosted()
    if expiry_date <= timezone.localdate():
        raise DomainError(
            f"Batch {batch_number} expires {expiry_date:%d %b %Y}.", code="batch_expired"
        )
    if rejected and not rejection_reason.strip():
        raise DomainError("A rejection needs a reason.", code="rejection_reason_required")

    accepted = received - rejected if accepted is None else accepted
    if accepted + rejected > received:
        raise DomainError(
            "Accepted plus rejected exceeds received.", code="receipt_split_invalid"
        )

    return GoodsReceiptLine.objects.create(
        receipt=receipt,
        order_line=order_line,
        product=product,
        uom=uom,
        ordered=order_line.quantity if order_line else 0,
        received=received,
        accepted=accepted,
        rejected=rejected,
        rejection_reason=rejection_reason,
        batch_number=batch_number,
        expiry_date=expiry_date,
        unit_cost_base=unit_cost_base,
        gtin=gtin,
        serial=serial,
    )


@transaction.atomic
def post_receipt(*, receipt: GoodsReceipt, performed_by: User) -> GoodsReceipt:
    """Create the batches and move the stock.

    This is the moment batches enter the system. Accepted quantity becomes
    available stock; rejected quantity is recorded on the line but never
    enters inventory, because it is going back on the lorry.
    """
    if receipt.status == GoodsReceiptStatus.POSTED:
        return receipt

    lines = list(receipt.lines.select_related("product", "uom", "order_line"))
    if not lines:
        raise DomainError("Add a line before posting.", code="receipt_empty")

    receipt.number = sequences.next_number(receipt.organization, "GOODS_RECEIPT")

    for line in lines:
        if line.accepted <= 0:
            continue

        batch, _ = Batch.objects.get_or_create(
            organization=receipt.organization,
            product=line.product,
            supplier=receipt.supplier,
            batch_number=line.batch_number,
            defaults={
                "expiry_date": line.expiry_date,
                "manufacture_date": line.manufacture_date,
                "unit_cost_base": line.unit_cost_base,
                "cold_chain": line.product.cold_chain,
                "gtin": line.gtin,
                "serial": line.serial,
                "created_by": performed_by,
            },
        )
        line.batch = batch
        line.save(update_fields=["batch"])

        inventory.post_movement(
            organization=receipt.organization,
            location=receipt.location,
            batch=batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(line.accepted, line.uom),
            performed_by=performed_by,
            reference=receipt.number,
            # A cold-chain delivery that arrived warm is quarantined, not
            # accepted — releasing it is a separate, deliberate decision.
            status=(
                StockStatus.QUARANTINED
                if line.product.cold_chain and receipt.transport_temperature_ok is False
                else StockStatus.AVAILABLE
            ),
            reason=(
                "Temperature breach on delivery"
                if line.product.cold_chain and receipt.transport_temperature_ok is False
                else ""
            ),
        )

        if line.order_line is not None:
            line.order_line.received_base += Quantity(line.accepted, line.uom).base_value
            line.order_line.save(update_fields=["received_base"])

    receipt.status = GoodsReceiptStatus.POSTED
    receipt.posted_at = timezone.now()
    receipt.modified_by = performed_by
    receipt.save(update_fields=["number", "status", "posted_at", "modified_by", "modified_at"])

    if receipt.order is not None:
        _update_order_progress(receipt.order)

    return receipt


def _update_order_progress(order: PurchaseOrder) -> None:
    lines = list(order.lines.all())
    if all(line.outstanding_base == 0 for line in lines):
        order.status = PurchaseOrderStatus.RECEIVED
    elif any(line.received_base > 0 for line in lines):
        order.status = PurchaseOrderStatus.PARTIALLY_RECEIVED
    else:
        return
    order.save(update_fields=["status", "modified_at"])


def discrepancies(receipt: GoodsReceipt) -> list[dict]:
    """What differed from the order. The point of the document."""
    rows = []
    for line in receipt.lines.select_related("product", "order_line"):
        if not line.is_short and line.rejected == 0:
            continue
        rows.append(
            {
                "product": line.product.name,
                "ordered": line.ordered,
                "received": line.received,
                "accepted": line.accepted,
                "rejected": line.rejected,
                "reason": line.rejection_reason,
                "short_by": max(0, line.ordered - line.received),
            }
        )
    return rows
