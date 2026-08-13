"""Commerce rules: listings, orders, receiving.

`post_receipt()` is where batches enter the system and where stock first
moves. Until it is called nothing has happened, which is what makes a
draft receipt safe to correct at the door.

See docs/05-modules.md §3–§5.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date

from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone

from catalog import checks as catalog_checks
from catalog.models import LegalStatus, Product, UnitOfMeasure
from commerce import checks
from core import alerts, audit, quotas, sequences
from core.capabilities import Capability, require_capability
from core.exceptions import DomainError, InsufficientStock
from core.models import Organization, PharmacistRegistration, User
from core import pricing
from core.money import Money
from core.quantity import Quantity, from_base
from commerce.models import (
    Availability,
    ControlledTransfer,
    GoodsReceipt,
    GoodsReceiptLine,
    GoodsReceiptStatus,
    ImportDocumentKind,
    OrderEvent,
    PriceTier,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    Shipment,
    ShipmentLine,
    ShipmentStatus,
    TradingRelationship,
    VendorListing,
)
from documents import services as documents
from inventory import services as inventory
from inventory.models import Batch, Location, MovementKind, StockStatus


log = logging.getLogger(__name__)


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


class InsufficientOffer(DomainError):
    default_code = "insufficient_offer"
    default_detail = "The depot has not offered that much."


class NotSellable(DomainError):
    default_code = "not_sellable_by_unit"
    default_detail = "The depot does not sell this product by that unit."


class NotApprover(DomainError):
    default_code = "not_approver"
    default_detail = "This order needs a different person to approve it."


class ControlledTransferRequired(DomainError):
    default_code = "controlled_transfer_required"
    default_detail = "A signed controlled substance transfer form is required."


# --------------------------------------------------------------------------
# Transitions
# --------------------------------------------------------------------------


def _transition(
    order: PurchaseOrder,
    *,
    to_status: str,
    actor: User | None,
    note: str = "",
    document_number: str = "",
    extra_fields: list[str] | None = None,
) -> PurchaseOrder:
    """The only place an order's status changes.

    Routing every assignment through one helper is what makes the
    timeline trustworthy: a status cannot move without leaving both an
    `OrderEvent` — which the counterparty sees — and an `AuditEvent`,
    which only the owning organization does.

    Writing them separately at each call site would work until somebody
    added a transition and forgot one. `test_every_transition_records`
    enumerates the services and asserts the pairing, so that omission
    fails the suite rather than the next audit.
    """
    from_status = order.status
    order.status = to_status
    order.modified_by = actor
    fields = ["status", "modified_by", "modified_at", *(extra_fields or [])]
    order.save(update_fields=list(dict.fromkeys(fields)))

    OrderEvent.objects.create(
        order=order,
        from_status=from_status,
        to_status=to_status,
        actor=actor,
        actor_organization_id=actor.organization_id if actor else None,
        note=note,
        document_number=document_number,
    )
    audit.record(
        action=f"commerce.order.{to_status.lower()}",
        subject=order,
        actor=actor,
        before={"status": from_status},
        after={"status": to_status, "number": order.number, "note": note},
        organization=order.organization,
    )
    return order


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
    srp: int | None = None,
    offered_base: int | None = None,
    performed_by: User | None = None,
    acknowledged: list[str] | None = None,
) -> VendorListing:
    """Offer a product for sale, and say how much of it.

    Only a wholesale pharmacy or importer may publish. A retail pharmacy
    that acquires a wholesale licence gains this without any code change,
    and loses it again when that licence lapses.

    `offered_base` is an allocation out of the depot's own stock, not a
    view of it. A depot holding 500 packs may offer 200 and keep the rest
    for its branches. Offering more than is held is refused: publishing a
    quantity you cannot ship is how a buyer plans around stock that was
    never there.
    """
    require_capability(organization, Capability.PUBLISH_LISTINGS)

    # A lapsed registration blocks the listing. Offering a product that
    # cannot legally be dispensed puts the buyer in breach, not us.
    alerts.enforce(
        catalog_checks.registration(product=product),
        organization=organization,
        performed_by=performed_by,
        acknowledged=acknowledged,
    )

    if offered_base is not None:
        if offered_base < 0:
            raise DomainError("Offered quantity cannot be negative.", code="negative_offer")
        on_hand = inventory.balance_for(organization=organization, product=product)
        if offered_base > on_hand:
            raise DomainError(
                f"Cannot offer more than is held: {on_hand} in stock.",
                code="offer_exceeds_stock",
                meta={"on_hand": on_hand, "offered": offered_base},
            )

    defaults = {
        "price": price,
        "price_uom": price_uom,
        "availability": availability,
        "moq": moq,
        "lead_time_days": lead_time_days,
        "is_active": True,
        "modified_by": performed_by,
    }
    # Only overwritten when given: a depot that reprices without touching
    # the suggested retail price has not withdrawn it.
    if srp is not None:
        defaults["srp"] = srp
    if offered_base is not None:
        defaults["offered_base"] = offered_base

    listing, created = VendorListing.objects.update_or_create(
        organization=organization,
        product=product,
        defaults=defaults,
    )
    audit.record(
        action="commerce.listing.published" if created else "commerce.listing.repriced",
        subject=listing,
        actor=performed_by,
        after={
            "product": product.name,
            "price": listing.price,
            "currency": listing.currency,
            "offered_base": listing.offered_base,
            "availability": listing.availability,
        },
        organization=organization,
    )
    return listing


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
    relationship = TradingRelationship.objects.filter(
        organization=supplier, customer=organization
    ).first()
    return PurchaseOrder.objects.create(
        organization=organization,
        supplier=supplier,
        deliver_to=deliver_to,
        required_by=required_by,
        # Frozen at creation. Renegotiating terms next month must not
        # quietly restate an order agreed under the old ones.
        payment_terms_days=relationship.payment_terms_days if relationship else 0,
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
    *,
    order: PurchaseOrder,
    listing: VendorListing,
    quantity: int,
    uom: UnitOfMeasure | None = None,
) -> PurchaseOrderLine:
    """Add a quantity of a listing, in the unit the buyer chose.

    A depot prices in one unit — usually the pack — but a buyer does not
    always want that unit. `uom` may be any level the depot is willing to
    sell at, and the price for it is derived from the listing price rather
    than stored separately, so a repricing cannot leave the two disagreeing.

    Every comparison below happens in **base units**. Minimum order and
    remaining allocation are quantities of medicine, not counts of
    whatever box the buyer happened to pick, and comparing pack counts
    against carton counts is how an order for a tenth of the intended
    amount gets accepted.
    """
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
    if quantity <= 0:
        raise DomainError("Quantity must be positive.", code="non_positive_quantity")

    chosen = uom or listing.price_uom
    if chosen.product_id != listing.product_id:
        raise DomainError("That unit belongs to another product.", code="uom_mismatch")
    if not chosen.is_sellable:
        raise NotSellable(
            f"{listing.organization.name} does not sell {listing.product.name} "
            f"by the {chosen.code.lower()}.",
            meta={"uom": chosen.code},
        )

    added_base = quantity * chosen.factor_to_base

    # One line per product and unit. Two lines of the same product in
    # different units is a legitimate order — a carton plus a loose pack —
    # so the unit is part of the identity, not just the price.
    #
    # Matched without the price. Volume breaks move the price as the
    # quantity grows, so including it would open a second line at the
    # moment a tier engaged and then deny the buyer the tier, because
    # neither line would reach the threshold alone.
    line = order.lines.filter(product=listing.product, uom=chosen).first()
    existing_base = line.quantity_base if line is not None else 0
    resulting_base = existing_base + added_base

    if added_base > listing.available_base:
        available = listing.available_base // chosen.factor_to_base
        raise InsufficientOffer(
            f"{listing.product.name}: {available} {chosen.code.lower()} available.",
            meta={"available_base": listing.available_base},
        )

    moq_base = listing.moq * listing.price_uom.factor_to_base
    if resulting_base < moq_base:
        raise BelowMinimum(
            f"Minimum order is {listing.moq} {listing.price_uom.code.lower()}.",
            meta={"moq_base": moq_base, "requested_base": resulting_base},
        )

    # Priced against the whole line, not the amount just added: a tier is
    # earned by what the buyer is ordering, not by how many clicks it
    # took them to get there.
    unit_price = _unit_price(listing, chosen, resulting_base)
    if line is not None:
        line.quantity += quantity
        line.quantity_base = resulting_base
        line.unit_price = unit_price
        line.line_total = unit_price * line.quantity
        line.save(
            update_fields=["quantity", "quantity_base", "unit_price", "line_total"]
        )
    else:
        line = PurchaseOrderLine.objects.create(
            order=order,
            product=listing.product,
            uom=chosen,
            quantity=quantity,
            quantity_base=added_base,
            unit_price=unit_price,
            line_total=unit_price * quantity,
        )
    _recalculate_order(order)
    return line


def tier_price(listing: VendorListing, quantity_base: int) -> int:
    """The listing price after any volume break the order qualifies for.

    Thresholds are in the listing's own unit, so the order quantity is
    converted down to that unit before comparing — a buyer ordering two
    cartons qualifies for a "24 packs or more" tier, and comparing carton
    count against a pack threshold would silently deny it.

    The best qualifying tier wins rather than the last one matched, so
    tiers entered out of order still price correctly.
    """
    qualifying = quantity_base // listing.price_uom.factor_to_base
    best = listing.price
    for tier in listing.tiers.all():
        if qualifying >= tier.min_quantity:
            best = min(best, tier.price)
    return best


def _unit_price(
    listing: VendorListing, uom: UnitOfMeasure, quantity_base: int | None = None
) -> int:
    """The listing price restated in `uom`.

    Derived, never stored: a listing that reprices must not leave a
    stale per-unit figure behind it. Breaking a pack down rounds up, so
    buying loose is never cheaper per unit than buying the pack — see
    core.pricing.
    """
    base_price = (
        listing.price if quantity_base is None else tier_price(listing, quantity_base)
    )
    if uom.id == listing.price_uom_id:
        return base_price
    return pricing.derive(
        Money(base_price, listing.currency),
        from_uom=listing.price_uom,
        to_uom=uom,
    ).price.amount


@transaction.atomic
def set_price_tiers(
    *,
    listing: VendorListing,
    tiers: list[tuple[int, int]],
    performed_by: User | None = None,
) -> list[PriceTier]:
    """Replace a listing's volume breaks.

    Refuses a tier priced at or above the one below it. A threshold that
    costs more per unit than a smaller order is either a typo or a trap,
    and either way a buyer who trips it has been overcharged for ordering
    more.
    """
    ordered = sorted(tiers)
    previous_price = listing.price
    for min_quantity, price in ordered:
        if min_quantity <= 1:
            raise DomainError(
                "A volume break starts above one.", code="tier_threshold_too_low"
            )
        if price >= previous_price:
            raise DomainError(
                f"The {min_quantity}+ tier is not cheaper than the one below it.",
                code="tier_not_cheaper",
                meta={"min_quantity": min_quantity, "price": price},
            )
        previous_price = price

    listing.tiers.all().delete()
    created = PriceTier.objects.bulk_create(
        [
            PriceTier(listing=listing, min_quantity=min_quantity, price=price)
            for min_quantity, price in ordered
        ]
    )
    audit.record(
        action="commerce.listing.tiers_set",
        subject=listing,
        actor=performed_by,
        after={"tiers": [{"min": q, "price": p} for q, p in ordered]},
        organization=listing.organization,
    )
    return created


def _recalculate_order(order: PurchaseOrder) -> None:
    order.subtotal = order.lines.aggregate(total=Sum("line_total"))["total"] or 0
    order.save(update_fields=["subtotal", "modified_at"])


@transaction.atomic
def request_approval(*, order: PurchaseOrder, performed_by: User) -> PurchaseOrder:
    """A pharmacist sends the order for internal release.

    The order does not reach the depot yet. Someone in the buying pharmacy
    with the authority to commit money has to release it first.
    """
    if order.status != PurchaseOrderStatus.DRAFT:
        raise DomainError("This order is no longer a draft.", code="order_not_draft")
    if not order.lines.exists():
        raise DomainError("Add a product first.", code="order_empty")

    return _transition(
        order, to_status=PurchaseOrderStatus.PENDING_APPROVAL, actor=performed_by
    )


@transaction.atomic
def reject_order(*, order: PurchaseOrder, performed_by: User, reason: str) -> PurchaseOrder:
    """Send it back to the pharmacist, with a reason.

    A rejection without a reason is an argument the raiser cannot answer,
    so the reason is required rather than optional.
    """
    if order.status != PurchaseOrderStatus.PENDING_APPROVAL:
        raise DomainError("This order is not awaiting approval.", code="order_not_pending")
    if not reason.strip():
        raise DomainError("Give a reason for the rejection.", code="reason_required")
    _assert_internal_approver(order, performed_by)

    order.rejected_by = performed_by
    order.rejected_at = timezone.now()
    order.reason = reason.strip()
    return _transition(
        order,
        to_status=PurchaseOrderStatus.REJECTED,
        actor=performed_by,
        note=order.reason,
        extra_fields=["rejected_by", "rejected_at", "reason"],
    )


@transaction.atomic
def reopen_order(*, order: PurchaseOrder, performed_by: User) -> PurchaseOrder:
    """A rejected order goes back to draft so it can be corrected."""
    if order.status != PurchaseOrderStatus.REJECTED:
        raise DomainError("Only a rejected order can be reopened.", code="order_not_rejected")
    return _transition(order, to_status=PurchaseOrderStatus.DRAFT, actor=performed_by)


def _assert_internal_approver(order: PurchaseOrder, user: User) -> bool:
    """Separation of duties, where there is anybody to separate from.

    The approver must belong to the buying pharmacy and must not be the
    person who raised the order — self-approval defeats the control
    entirely.

    Except where the pharmacy is one person. A great many Rwandan retail
    pharmacies are a single registered pharmacist who is also the owner,
    and refusing them outright does not produce a second approver: it
    produces a second login sharing one keyboard, which is the same risk
    with the audit trail switched off. So where no colleague exists the
    approval proceeds and is **recorded as self-approved**, and the
    control returns the moment a second account is created.

    Returns whether a second person actually approved it, so the caller
    can say so on the record.

    This is still weaker than it should be: there is no role model yet,
    so any second colleague can release an order. A proper
    APPROVE_PURCHASE permission belongs here once roles exist.
    """
    if user.organization_id != order.organization_id:
        raise DomainError("Only the buying pharmacy can approve.", code="not_buyer")
    if not order.created_by_id or order.created_by_id != user.id:
        return True

    colleagues = (
        User.objects.filter(organization_id=order.organization_id, is_active=True)
        .exclude(pk=user.pk)
        .exists()
    )
    if colleagues:
        raise NotApprover("An order cannot be approved by the person who raised it.")
    return False


@transaction.atomic
def submit_order(*, order: PurchaseOrder, performed_by: User) -> PurchaseOrder:
    """Internal approval. Releases the order to the depot.

    Called by the owner or manager, not the pharmacist who raised it.
    """
    if order.status != PurchaseOrderStatus.PENDING_APPROVAL:
        raise DomainError("This order is not awaiting approval.", code="order_not_pending")
    if not order.lines.exists():
        raise DomainError("Add a product before submitting.", code="order_empty")
    second_pair_of_eyes = _assert_internal_approver(order, performed_by)

    relationship = TradingRelationship.objects.filter(
        organization=order.supplier, customer=order.organization, is_active=True
    ).first()
    if relationship is None or not relationship.is_verified:
        raise CustomerNotVerified(
            f"{order.supplier.name} has not verified this pharmacy.",
            meta={"supplier_id": str(order.supplier_id)},
        )

    order.number = sequences.next_number(order.organization, "PURCHASE_ORDER")
    order.submitted_at = timezone.now()
    order.approved_by = performed_by
    order.approved_at = timezone.now()
    return _transition(
        order,
        to_status=PurchaseOrderStatus.SUBMITTED,
        actor=performed_by,
        document_number=order.number,
        # Said plainly on the event rather than inferred later by
        # comparing two user ids in the audit stream.
        note="" if second_pair_of_eyes else "Self-approved: sole user of this pharmacy.",
        extra_fields=["number", "submitted_at", "approved_by", "approved_at"],
    )


@transaction.atomic
def confirm_order(
    *,
    order: PurchaseOrder,
    performed_by: User,
    acknowledged: list[str] | None = None,
    reason: str = "",
) -> PurchaseOrder:
    """The supplier accepts. Only the supplier may do this."""
    if order.supplier_id != performed_by.organization_id:
        raise DomainError("Only the supplier can confirm an order.", code="not_supplier")
    if order.status != PurchaseOrderStatus.SUBMITTED:
        raise DomainError("This order is not awaiting confirmation.", code="order_not_submitted")

    # Credit and licence are checked here, counting this order. Checking
    # only historic debt lets a pharmacy sitting at its limit place one
    # more order every time, because the new one is never part of the sum.
    #
    # Both go through the alert framework: over the limit is a refusal,
    # approaching it is a warning the depot may accept with a reason, and
    # a lapsed licence is a refusal. One mechanism, three severities.
    alerts.enforce(
        [
            *checks.buyer_licence(customer=order.organization),
            *checks.credit(
                supplier=order.supplier,
                customer=order.organization,
                pending=order.subtotal,
            ),
        ],
        organization=order.supplier,
        performed_by=performed_by,
        acknowledged=acknowledged or [],
        reason=reason,
    )

    # Approving reserves the goods against the offer. Until now the
    # quantity was only requested; two buyers could each ask for the last
    # 200 packs and both be told yes.
    for line in order.lines.select_related("product"):
        listing = VendorListing.objects.select_for_update().filter(
            organization=order.supplier, product=line.product
        ).first()
        if listing is None:
            continue
        if line.quantity_base > listing.available_base:
            raise InsufficientOffer(
                f"{line.product.name} is no longer available in that quantity.",
                meta={"available_base": listing.available_base},
            )
        listing.committed_base += line.quantity_base
        listing.save(update_fields=["committed_base"])

    order.confirmed_at = timezone.now()
    order.approved_by = performed_by
    order.approved_at = timezone.now()
    return _transition(
        order,
        to_status=PurchaseOrderStatus.CONFIRMED,
        actor=performed_by,
        extra_fields=["confirmed_at", "approved_by", "approved_at"],
    )


@transaction.atomic
def start_preparation(*, order: PurchaseOrder, performed_by: User) -> PurchaseOrder:
    """The depot begins picking. Visible to the buyer as tracking."""
    if order.supplier_id != performed_by.organization_id:
        raise DomainError("Only the depot can prepare an order.", code="not_supplier")
    if order.status != PurchaseOrderStatus.CONFIRMED:
        raise DomainError("Only an approved order can be prepared.", code="order_not_confirmed")

    return _transition(order, to_status=PurchaseOrderStatus.PREPARING, actor=performed_by)


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
    vehicle_registration: str = "",
    driver_name: str = "",
    driver_licence: str = "",
    controlled_transfer: PharmacistRegistration | None = None,
    acknowledged: list[str] | None = None,
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
        PurchaseOrderStatus.PREPARING,
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

    # A scheduled drug does not leave without two named pharmacists on
    # the form. Checked before anything moves, because a stock movement
    # posted and then rolled back still had to be reasoned about.
    controlled = [line for line in outstanding if line.product.legal_status == LegalStatus.CONTROLLED]
    if controlled and controlled_transfer is None:
        raise ControlledTransferRequired(
            f"{controlled[0].product.name} is a controlled drug. "
            "Raise a transfer form first.",
            meta={"products": [line.product.name for line in controlled]},
        )

    # The regulator caps throughput per schedule per period, so the
    # consignment about to leave is counted with what already has.
    # Checking only history lets a depot at its limit ship one more
    # consignment every time.
    if controlled:
        pending: dict[str, int] = {}
        for line in controlled:
            pending[line.product.controlled_schedule] = (
                pending.get(line.product.controlled_schedule, 0)
                + line.undispatched_base
            )
        alerts.enforce(
            [
                alert
                for schedule, quantity in pending.items()
                for alert in quotas.check(
                    organization=order.supplier,
                    schedule=schedule,
                    pending_base=quantity,
                )
            ],
            organization=order.supplier,
            performed_by=performed_by,
            acknowledged=acknowledged or [],
        )

    shipment = Shipment.objects.create(
        organization=order.supplier,
        order=order,
        from_location=from_location,
        carrier=carrier,
        vehicle_registration=vehicle_registration,
        driver_name=driver_name,
        driver_licence=driver_licence,
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

        # Shipping consumes the offer, not just the reservation: goods
        # that have left cannot be offered to anyone else.
        listing = VendorListing.objects.select_for_update().filter(
            organization=order.supplier, product=line.product
        ).first()
        if listing is not None:
            listing.offered_base = max(0, listing.offered_base - take)
            listing.committed_base = max(0, listing.committed_base - take)
            listing.save(update_fields=["offered_base", "committed_base"])

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

    if controlled:
        transfer = ControlledTransfer.objects.create(
            organization=order.supplier,
            shipment=shipment,
            released_by=controlled_transfer,
            released_at=timezone.now(),
            number=sequences.next_number(order.supplier, "CONTROLLED_TRANSFER"),
            created_by=performed_by,
        )
        documents.issue_controlled_transfer(transfer=transfer, performed_by=performed_by)

    # The paperwork the goods travel with. Issued here rather than on
    # demand so the document is frozen against what actually shipped —
    # rendering it later would restate whatever the records say by then.
    documents.issue_picking_ticket(shipment=shipment, performed_by=performed_by)
    documents.issue_delivery_note(shipment=shipment, performed_by=performed_by)

    _notify_buyer(shipment=shipment, performed_by=performed_by)

    audit.record(
        action="commerce.shipment.dispatched",
        subject=shipment,
        actor=performed_by,
        after={
            "number": shipment.number,
            "order": order.number,
            "lines": shipment.lines.count(),
            "carrier": carrier,
        },
        organization=order.supplier,
    )

    _update_dispatch_progress(
        order, performed_by=performed_by, document_number=shipment.number
    )
    return shipment


def _notify_buyer(*, shipment: Shipment, performed_by: User) -> None:
    """Seed the buyer's goods receipt from what actually shipped.

    Both organizations are on this instance, so the advance notice is a
    service call rather than a file. The buyer still receives a **draft**:
    nothing enters their ledger until someone has counted the cartons.

    A failure here must not roll back the dispatch. The goods have left;
    the delivery note is issued and the stock has moved. A missing
    pre-fill means the receiver types the delivery note in by hand, which
    is exactly where they were before this existed.
    """
    from commerce import payloads

    order = shipment.order
    try:
        # A savepoint, so a failure here rolls back the pre-fill without
        # poisoning the dispatch transaction around it.
        with transaction.atomic():
            payloads.apply_transfer_payload(
                payload=payloads.build_transfer_payload(shipment=shipment),
                organization=order.organization,
                location=order.deliver_to,
                performed_by=performed_by,
                order=order,
            )
    except Exception:
        log.exception(
            "Advance notice failed for %s; the buyer will key the delivery note",
            shipment.number,
        )


def _update_dispatch_progress(
    order: PurchaseOrder, *, performed_by: User | None = None, document_number: str = ""
) -> None:
    # Query the lines rather than reading order.lines.all(): the viewset
    # prefetches that relation, so the cached rows still carry the
    # pre-dispatch tallies and a fully shipped order would be left
    # PARTIALLY_DISPATCHED forever.
    lines = list(PurchaseOrderLine.objects.filter(order=order))
    complete = all(line.undispatched_base == 0 for line in lines)
    _transition(
        order,
        to_status=(
            PurchaseOrderStatus.DISPATCHED if complete
            else PurchaseOrderStatus.PARTIALLY_DISPATCHED
        ),
        actor=performed_by,
        document_number=document_number,
    )


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


def _to_rwf(amount: int, receipt: GoodsReceipt) -> int:
    """An invoiced amount restated in RWF.

    The rate is stored scaled by 10,000 and applied with integer
    arithmetic — a float here would drift by a franc or two per line and
    the drift would land in stock valuation, where nobody would look for
    it.
    """
    if receipt.invoice_currency == "RWF":
        return amount
    return amount * receipt.fx_rate_scaled // 10_000


def apportion_landed_cost(receipt: GoodsReceipt) -> dict[str, int]:
    """Spread freight, duty and clearing across the accepted lines.

    **By value, not by count.** A carton of insulin and a carton of gauze
    do not consume the same share of a customs bill, and splitting evenly
    would move cost from the expensive line to the cheap one — quietly
    inverting which products look profitable.

    `Money.allocate` distributes the remainder a franc at a time, largest
    share first, so the parts sum to the whole exactly. Landed cost that
    does not reconcile is capital that has gone missing from the books.
    """
    lines = [line for line in receipt.lines.all() if line.accepted > 0]
    if not lines:
        return {}

    charges = receipt.landed_charges
    if charges <= 0:
        return {str(line.id): 0 for line in lines}

    weights = [_to_rwf(line.unit_cost_base, receipt) * line.accepted * line.uom.factor_to_base
               for line in lines]
    if sum(weights) == 0:
        # Free goods — samples or donations — still carry freight, so
        # fall back to splitting by quantity rather than refusing.
        weights = [line.accepted * line.uom.factor_to_base for line in lines]

    shares = Money(charges, "RWF").allocate(weights)
    return {str(line.id): share.amount for line, share in zip(lines, shares)}


def _quarantine_reason(*, receipt: GoodsReceipt, line, batch) -> tuple[bool, str]:
    """Whether this batch is held on arrival, and why.

    Three reasons, in the order they matter:

    1. **A recorded cold-chain breach.** The log says the consignment left
       its range in transit. Not a warning — by the time anyone reads a
       warning the product is already damaged.
    2. **A cold-chain delivery whose temperature was not confirmed** at
       the door. Unconfirmed is not the same as fine.
    3. **A regulated product with no Certificate of Analysis.** Nobody
       can say the batch met specification, and a batch nobody has tested
       is not stock, it is a liability.
    """
    breached = receipt.import_documents.filter(
        kind=ImportDocumentKind.COLD_CHAIN_LOG, breach=True
    ).exists()
    if breached:
        return True, "Cold-chain excursion recorded in transit"

    if line.product.cold_chain and receipt.transport_temperature_ok is False:
        return True, "Temperature breach on delivery"

    # Only a registered medicine needs one. Consumables and cosmetics have
    # no registration, and holding every box of plasters for a certificate
    # nobody issues would empty the shelves rather than protect anyone.
    needs_coa = getattr(line.product, "registration", None) is not None
    if needs_coa:
        has_coa = receipt.import_documents.filter(
            kind=ImportDocumentKind.CERTIFICATE_OF_ANALYSIS
        ).filter(models.Q(batch=batch) | models.Q(batch__isnull=True)).exists()
        # An import receipt is one raised against a foreign consignment;
        # a domestic delivery from a local depot carries the depot's own
        # release, not a manufacturer's certificate.
        is_import = receipt.invoice_currency != "RWF" or receipt.landed_charges > 0
        if is_import and not has_coa:
            return True, "No certificate of analysis on file for this batch"

    return False, ""


@transaction.atomic
def release_batch(
    *,
    batch,
    location: Location,
    organization: Organization,
    performed_by: User,
    reason: str,
) -> None:
    """Move quarantined stock into available, with a recorded reason.

    Two movements rather than a status update, because the ledger is the
    record: a batch that was held and then released has a history, and
    an in-place edit would erase the fact that it was ever in doubt.
    """
    if not reason.strip():
        raise DomainError("Give a reason for the release.", code="reason_required")

    held = inventory.balance_for(
        organization=organization,
        product=batch.product,
        location=location,
        status=StockStatus.QUARANTINED,
    )
    if held <= 0:
        raise DomainError("Nothing is quarantined here.", code="nothing_quarantined")

    quantity = from_base(held, batch.product.base_uom)
    inventory.post_movement(
        organization=organization,
        location=location,
        batch=batch,
        kind=MovementKind.RELEASE,
        quantity=-quantity,
        performed_by=performed_by,
        status=StockStatus.QUARANTINED,
        reason=reason.strip(),
    )
    inventory.post_movement(
        organization=organization,
        location=location,
        batch=batch,
        kind=MovementKind.RELEASE,
        quantity=quantity,
        performed_by=performed_by,
        status=StockStatus.AVAILABLE,
        reason=reason.strip(),
    )
    audit.record(
        action="inventory.batch.released",
        subject=batch,
        actor=performed_by,
        after={
            "batch": batch.batch_number,
            "quantity_base": held,
            "location": location.name,
            "reason": reason.strip(),
        },
        organization=organization,
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
    shares = apportion_landed_cost(receipt)

    for line in lines:
        if line.accepted <= 0:
            continue

        # What this stock actually cost to have on the shelf: the invoice
        # converted to RWF, plus its share of getting it here.
        share = shares.get(str(line.id), 0)
        accepted_base = Quantity(line.accepted, line.uom).base_value
        landed_unit_cost = _to_rwf(line.unit_cost_base, receipt) + (
            share // accepted_base if accepted_base else 0
        )
        line.landed_cost_share = share

        batch, _ = Batch.objects.get_or_create(
            organization=receipt.organization,
            product=line.product,
            supplier=receipt.supplier,
            batch_number=line.batch_number,
            defaults={
                "expiry_date": line.expiry_date,
                "manufacture_date": line.manufacture_date,
                "unit_cost_base": landed_unit_cost,
                "cold_chain": line.product.cold_chain,
                "gtin": line.gtin,
                "serial": line.serial,
                "created_by": performed_by,
            },
        )
        line.batch = batch
        line.save(update_fields=["batch", "landed_cost_share"])

        held, reason = _quarantine_reason(receipt=receipt, line=line, batch=batch)
        inventory.post_movement(
            organization=receipt.organization,
            location=receipt.location,
            batch=batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(line.accepted, line.uom),
            performed_by=performed_by,
            reference=receipt.number,
            # Quarantine is the default for anything unproven. Releasing
            # it is a separate, deliberate, recorded decision.
            status=StockStatus.QUARANTINED if held else StockStatus.AVAILABLE,
            reason=reason,
        )

        if line.order_line is not None:
            line.order_line.received_base += Quantity(line.accepted, line.uom).base_value
            line.order_line.save(update_fields=["received_base"])

    receipt.status = GoodsReceiptStatus.POSTED
    receipt.posted_at = timezone.now()
    receipt.modified_by = performed_by
    receipt.save(update_fields=["number", "status", "posted_at", "modified_by", "modified_at"])

    documents.issue_goods_receipt_note(receipt=receipt, performed_by=performed_by)

    audit.record(
        action="commerce.receipt.posted",
        subject=receipt,
        actor=performed_by,
        after={
            "number": receipt.number,
            "lines": len(lines),
            "landed_charges": receipt.landed_charges,
            "order": receipt.order.number if receipt.order else "",
        },
        organization=receipt.organization,
    )

    if receipt.order is not None:
        _update_order_progress(
            receipt.order, performed_by=performed_by, document_number=receipt.number
        )

    return receipt


def _update_order_progress(
    order: PurchaseOrder, *, performed_by: User | None = None, document_number: str = ""
) -> None:
    lines = list(PurchaseOrderLine.objects.filter(order=order))
    if all(line.outstanding_base == 0 for line in lines):
        to_status = PurchaseOrderStatus.RECEIVED
    elif any(line.received_base > 0 for line in lines):
        to_status = PurchaseOrderStatus.PARTIALLY_RECEIVED
    else:
        return
    _transition(
        order, to_status=to_status, actor=performed_by, document_number=document_number
    )


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
