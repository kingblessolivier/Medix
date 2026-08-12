"""Commerce: listings, purchase orders, receiving.

This is where the two pharmacy types meet. A wholesale pharmacy publishes
listings; a retail pharmacy raises orders against them. Both are Rwanda
FDA–licensed premises sharing the same catalog, batches and ledger — they
differ in who they sell to, and that difference is expressed as **held
licences**, never a type field. See ADR-006.

Receiving is where batches enter the system, and where a shortfall
becomes a discrepancy report rather than a silently edited number.

See docs/05-modules.md §2–§5.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from catalog.models import Product, UnitOfMeasure
from core.models import BaseModel, TenantModel


class Availability(models.TextChoices):
    """Three states, each a different transaction.

    NOT_IN_COUNTRY is not an error — it is the state that turns a dead end
    into an import request, which is the platform's whole point.
    """

    AVAILABLE_NOW = "AVAILABLE_NOW", "Available now"
    INCOMING = "INCOMING", "Incoming"
    PRE_ORDER = "PRE_ORDER", "Pre-order"
    IMPORT_ON_DEMAND = "IMPORT_ON_DEMAND", "Import on demand"
    NOT_IN_COUNTRY = "NOT_IN_COUNTRY", "Not currently in Rwanda"


class VendorListing(TenantModel):
    """What a depot offers, and how much of it.

    A depot's holding and its offer are different numbers. A depot with
    500 packs may publish 200 and keep the rest for its own branches or a
    standing contract, so `offered_base` is an allocation out of stock,
    not a view of it.

    Publishing the true balance instead would also tell every customer —
    and every competitor with an account — exactly what the depot holds.
    The marketplace shows this figure, never the stock ledger.
    """

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="listings")
    availability = models.CharField(
        max_length=20, choices=Availability.choices, default=Availability.AVAILABLE_NOW
    )

    price = models.BigIntegerField(help_text="Per price_uom, minor units")
    currency = models.CharField(max_length=3, default="RWF")
    price_uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")

    moq = models.IntegerField(default=1, help_text="Minimum order quantity, in price_uom")
    lead_time_days = models.IntegerField(default=1)
    is_active = models.BooleanField(default=True)

    #: Allocated to the public catalogue, in base units. Decremented when
    #: goods are dispatched, so the offer shrinks as it is consumed.
    offered_base = models.BigIntegerField(default=0)
    #: Reserved by confirmed orders not yet shipped. Offered minus this is
    #: what a new buyer can actually take.
    committed_base = models.BigIntegerField(default=0)

    class Meta:
        db_table = "commerce_vendor_listing"
        ordering = ["price"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "product"], name="uq_listing_per_vendor_product"
            ),
            models.CheckConstraint(condition=models.Q(price__gte=0), name="ck_listing_price"),
            models.CheckConstraint(condition=models.Q(moq__gte=1), name="ck_listing_moq"),
            models.CheckConstraint(
                condition=models.Q(offered_base__gte=0), name="ck_listing_offered_nonneg"
            ),
            models.CheckConstraint(
                condition=models.Q(committed_base__gte=0), name="ck_listing_committed_nonneg"
            ),
        ]
        indexes = [models.Index(fields=["product", "availability", "price"])]

    def __str__(self) -> str:
        return f"{self.product.name} @ {self.organization.name}"

    @property
    def is_orderable(self) -> bool:
        """Import-on-demand and not-in-country go through a request, not an order."""
        return self.availability in (Availability.AVAILABLE_NOW, Availability.INCOMING)

    @property
    def available_base(self) -> int:
        """What a new order may take: offered, less what is already spoken for."""
        return max(0, self.offered_base - self.committed_base)


class TradingRelationship(TenantModel):
    """A wholesale pharmacy's approved customer.

    A supplier must verify that a buyer holds a current licence before
    supplying it — that check is the point of this record, not the
    convenience of a saved contact.
    """

    customer = models.ForeignKey(
        "core.Organization", on_delete=models.PROTECT, related_name="supplier_relationships"
    )
    credit_limit = models.BigIntegerField(default=0)
    payment_terms_days = models.IntegerField(default=0)
    is_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "commerce_trading_relationship"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "customer"], name="uq_trading_relationship"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.customer.name} → {self.organization.name}"


# --------------------------------------------------------------------------
# Purchase orders
# --------------------------------------------------------------------------


class PurchaseOrderStatus(models.TextChoices):
    """Two approvals, then the depot picks it.

    The buyer's own approval comes first: a pharmacist raises the order
    and an owner or manager releases it. Only then does the depot see it,
    and the depot approves again before anything is picked. Collapsing
    those into one "submitted" hides which side is holding the order up.
    """

    DRAFT = "DRAFT", "Draft"
    PENDING_APPROVAL = "PENDING_APPROVAL", "Awaiting internal approval"
    REJECTED = "REJECTED", "Rejected internally"
    SUBMITTED = "SUBMITTED", "Sent to depot"
    CONFIRMED = "CONFIRMED", "Approved by depot"
    PREPARING = "PREPARING", "Being prepared"
    PARTIALLY_DISPATCHED = "PARTIALLY_DISPATCHED", "Partially dispatched"
    DISPATCHED = "DISPATCHED", "Dispatched"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED", "Partially received"
    RECEIVED = "RECEIVED", "Received"
    CANCELLED = "CANCELLED", "Cancelled"


class PurchaseOrder(TenantModel):
    """Raised by the buyer, against a supplier.

    Visible to both sides: the buyer sees it in Orders, the supplier in
    their fulfilment queue. Cross-organization visibility is modelled
    explicitly rather than by relaxing the tenant filter.
    """

    number = models.CharField(max_length=30, blank=True)
    supplier = models.ForeignKey(
        "core.Organization", on_delete=models.PROTECT, related_name="incoming_orders"
    )
    deliver_to = models.ForeignKey(
        "inventory.Location", on_delete=models.PROTECT, related_name="+"
    )

    status = models.CharField(
        max_length=20, choices=PurchaseOrderStatus.choices, default=PurchaseOrderStatus.DRAFT
    )
    required_by = models.DateField(null=True, blank=True)

    subtotal = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="RWF")

    submitted_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "commerce_purchase_order"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "number"],
                condition=~models.Q(number=""),
                name="uq_po_number",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["supplier", "status"]),
        ]

    def __str__(self) -> str:
        return self.number or f"draft PO {self.id}"


class PurchaseOrderLine(BaseModel):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+")
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")

    quantity = models.IntegerField()
    quantity_base = models.BigIntegerField()
    unit_price = models.BigIntegerField()
    line_total = models.BigIntegerField()

    #: Running tally, so a partial delivery is visible without recomputing.
    received_base = models.BigIntegerField(default=0)
    #: What the supplier has actually shipped. Diverges from received_base
    #: whenever goods are in transit or a delivery arrives short.
    dispatched_base = models.BigIntegerField(default=0)

    class Meta:
        db_table = "commerce_purchase_order_line"
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="ck_po_line_qty"),
            models.CheckConstraint(
                condition=models.Q(received_base__gte=0), name="ck_po_received_nonneg"
            ),
            models.CheckConstraint(
                condition=models.Q(dispatched_base__gte=0), name="ck_po_dispatched_nonneg"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product.name} x{self.quantity}"

    @property
    def outstanding_base(self) -> int:
        return max(0, self.quantity_base - self.received_base)

    @property
    def undispatched_base(self) -> int:
        """Ordered but not yet shipped — what the supplier still owes."""
        return max(0, self.quantity_base - self.dispatched_base)


# --------------------------------------------------------------------------
# Receiving
# --------------------------------------------------------------------------


class GoodsReceiptStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    POSTED = "POSTED", "Posted"


class GoodsReceipt(TenantModel):
    """Where batches enter the system.

    Posting a receipt creates the batches and the ledger movements. Until
    it is posted, nothing has moved.
    """

    number = models.CharField(max_length=30, blank=True)
    order = models.ForeignKey(
        PurchaseOrder, null=True, blank=True, on_delete=models.PROTECT, related_name="receipts"
    )
    supplier = models.ForeignKey(
        "core.Organization", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    location = models.ForeignKey(
        "inventory.Location", on_delete=models.PROTECT, related_name="receipts"
    )

    status = models.CharField(
        max_length=10, choices=GoodsReceiptStatus.choices, default=GoodsReceiptStatus.DRAFT
    )
    received_on = models.DateField(default=timezone.localdate)
    posted_at = models.DateTimeField(null=True, blank=True)

    #: Confirmed at the door for a cold-chain delivery. A breach quarantines
    #: rather than accepts.
    transport_temperature_ok = models.BooleanField(null=True, blank=True)

    class Meta:
        db_table = "commerce_goods_receipt"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "number"],
                condition=~models.Q(number=""),
                name="uq_grn_number",
            ),
        ]

    def __str__(self) -> str:
        return self.number or f"draft GRN {self.id}"

    @property
    def has_discrepancy(self) -> bool:
        return any(line.is_short or line.rejected > 0 for line in self.lines.all())


class GoodsReceiptLine(BaseModel):
    """Ordered, received, accepted, rejected — four distinct numbers.

    The discrepancy is the point of the document, so it is never collapsed
    into a single received figure.
    """

    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="lines")
    order_line = models.ForeignKey(
        PurchaseOrderLine, null=True, blank=True, on_delete=models.PROTECT, related_name="receipts"
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+")
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")

    ordered = models.IntegerField(default=0)
    received = models.IntegerField()
    accepted = models.IntegerField()
    rejected = models.IntegerField(default=0)
    rejection_reason = models.CharField(max_length=200, blank=True)

    batch_number = models.CharField(max_length=60)
    expiry_date = models.DateField()
    manufacture_date = models.DateField(null=True, blank=True)
    unit_cost_base = models.BigIntegerField(default=0)

    #: Populated by a GS1 scan rather than typed off the box.
    gtin = models.CharField(max_length=14, blank=True)
    serial = models.CharField(max_length=40, blank=True)

    batch = models.ForeignKey(
        "inventory.Batch", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        db_table = "commerce_goods_receipt_line"
        constraints = [
            models.CheckConstraint(condition=models.Q(received__gte=0), name="ck_grn_received"),
            models.CheckConstraint(
                condition=models.Q(accepted__gte=0) & models.Q(rejected__gte=0),
                name="ck_grn_split_nonneg",
            ),
            models.CheckConstraint(
                condition=models.Q(accepted__lte=models.F("received")),
                name="ck_grn_accepted_within_received",
            ),
            # A rejection without a reason is not a record, it is a gap.
            models.CheckConstraint(
                condition=models.Q(rejected=0) | ~models.Q(rejection_reason=""),
                name="ck_grn_rejection_reason",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product.name} {self.batch_number}"

    @property
    def is_short(self) -> bool:
        return self.ordered > 0 and self.received < self.ordered


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------


class ShipmentStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    DISPATCHED = "DISPATCHED", "Dispatched"


class Shipment(TenantModel):
    """The supplier's delivery note.

    Dispatch is the other half of receiving. Without it the buyer's ledger
    gains stock that never left the supplier's, so the same goods exist
    twice on the platform and every marketplace stock figure overstates.

    Owned by the *supplier* — this is their document, raised against the
    buyer's order.
    """

    number = models.CharField(max_length=30, blank=True)
    order = models.ForeignKey(
        PurchaseOrder, on_delete=models.PROTECT, related_name="shipments"
    )
    from_location = models.ForeignKey(
        "inventory.Location", on_delete=models.PROTECT, related_name="+"
    )
    status = models.CharField(
        max_length=20, choices=ShipmentStatus.choices, default=ShipmentStatus.DRAFT
    )
    dispatched_at = models.DateTimeField(null=True, blank=True)
    carrier = models.CharField(max_length=120, blank=True)

    class Meta:
        db_table = "commerce_shipment"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "number"],
                condition=~models.Q(number=""),
                name="uq_shipment_number",
            ),
        ]
        indexes = [models.Index(fields=["organization", "status"])]

    def __str__(self) -> str:
        return self.number or f"draft shipment {self.id}"


class ShipmentLine(BaseModel):
    """One batch actually picked.

    A single order line can span several batches — FEFO does not care that
    the buyer ordered a round number — so this is per batch, not per order
    line. The batch number and expiry recorded here are what the receiving
    pharmacy checks the physical cartons against.
    """

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="lines")
    order_line = models.ForeignKey(
        PurchaseOrderLine, on_delete=models.PROTECT, related_name="dispatches"
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+")
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")

    quantity_base = models.BigIntegerField()

    batch = models.ForeignKey("inventory.Batch", on_delete=models.PROTECT, related_name="+")
    batch_number = models.CharField(max_length=60)
    expiry_date = models.DateField()

    class Meta:
        db_table = "commerce_shipment_line"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity_base__gt=0), name="ck_shipment_line_qty"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product.name} {self.batch_number}"
