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

    #: Copied from the trading relationship when the order is raised, so
    #: renegotiating terms later does not silently restate old orders.
    payment_terms_days = models.IntegerField(default=0)

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

    # -- import: what it cost to get the goods here ------------------------
    #
    # A depot's capital is not the invoice. Freight, duty and clearing are
    # real money spent acquiring the stock, and if they sit beside the
    # batch instead of inside it every downstream margin is overstated.
    # They are apportioned into unit cost when the receipt posts.
    #
    # Charges are held in RWF because that is where they are incurred —
    # a clearing agent in Kigali does not bill in euro — while the goods
    # themselves may be invoiced in anything.
    invoice_number = models.CharField(max_length=60, blank=True)
    invoice_currency = models.CharField(max_length=3, default="RWF")
    #: Foreign currency per 1 RWF is unusable at these magnitudes, so this
    #: is RWF per one unit of invoice_currency, scaled by 10,000 to keep
    #: it an integer. 1 USD = 1,320.50 RWF is stored as 13,205,000.
    fx_rate_scaled = models.BigIntegerField(default=10_000)
    fx_rate_date = models.DateField(null=True, blank=True)
    #: True when the rate is the published official one rather than an
    #: indicative quote. An estimate must not be mistaken for a fact.
    fx_rate_is_official = models.BooleanField(default=False)

    freight = models.BigIntegerField(default=0)
    customs_duty = models.BigIntegerField(default=0)
    clearing_fees = models.BigIntegerField(default=0)
    other_charges = models.BigIntegerField(default=0)
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

    @property
    def landed_charges(self) -> int:
        """Everything spent getting the goods here, in RWF minor units."""
        return self.freight + self.customs_duty + self.clearing_fees + self.other_charges

    @property
    def fx_rate(self) -> float:
        """For display only. Never use this for money arithmetic."""
        return self.fx_rate_scaled / 10_000


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

    #: Share of the receipt's freight, duty and clearing, in RWF minor
    #: units. Written when the receipt posts.
    landed_cost_share = models.BigIntegerField(default=0)

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


# --------------------------------------------------------------------------
# Invoicing and payment
# --------------------------------------------------------------------------


class InvoiceKind(models.TextChoices):
    """A proforma is a request; a tax invoice is a debt.

    They are deliberately different documents. A proforma is issued before
    goods move — a new pharmacy, or a controlled line — and creates no
    receivable. Treating one as the other either overstates what is owed
    or lets goods leave against a document that never demanded payment.
    """

    PROFORMA = "PROFORMA", "Proforma invoice"
    TAX = "TAX", "Commercial tax invoice"
    CREDIT_NOTE = "CREDIT_NOTE", "Credit note"


class InvoiceStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ISSUED = "ISSUED", "Issued"
    PART_PAID = "PART_PAID", "Partly paid"
    PAID = "PAID", "Paid"
    CANCELLED = "CANCELLED", "Cancelled"


class Invoice(TenantModel):
    """What the depot is owed, and by when.

    Owned by the seller. Totals are stored because an invoice is a legal
    document that must still read the same in five years — the tax rate
    that applied on the day is baked in, not re-derived from whatever the
    rule table says later.
    """

    number = models.CharField(max_length=30, blank=True)
    kind = models.CharField(max_length=20, choices=InvoiceKind.choices, default=InvoiceKind.TAX)
    status = models.CharField(
        max_length=20, choices=InvoiceStatus.choices, default=InvoiceStatus.DRAFT
    )

    order = models.ForeignKey(
        PurchaseOrder, null=True, blank=True, on_delete=models.PROTECT, related_name="invoices"
    )
    customer = models.ForeignKey(
        "core.Organization", on_delete=models.PROTECT, related_name="purchase_invoices"
    )

    issued_on = models.DateField(null=True, blank=True)
    #: issued_on + the terms agreed when the order was raised.
    due_on = models.DateField(null=True, blank=True)
    payment_terms_days = models.IntegerField(default=0)

    subtotal = models.BigIntegerField(default=0)
    tax_total = models.BigIntegerField(default=0)
    total = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="RWF")

    class Meta:
        db_table = "commerce_invoice"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "number"],
                condition=~models.Q(number=""),
                name="uq_invoice_number",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["customer", "status"]),
        ]

    def __str__(self) -> str:
        return self.number or f"draft invoice {self.id}"

    @property
    def settled(self) -> int:
        """Paid so far. Queried, never read off a cached relation."""
        return (
            InvoicePayment.objects.filter(invoice=self).aggregate(
                total=models.Sum("amount")
            )["total"]
            or 0
        )

    @property
    def outstanding(self) -> int:
        return max(0, self.total - self.settled)

    def days_overdue(self, *, as_of=None) -> int:
        as_of = as_of or timezone.localdate()
        if self.due_on is None or self.outstanding == 0:
            return 0
        return max(0, (as_of - self.due_on).days)


class InvoiceLine(BaseModel):
    """One line, with the tax that applied on the day it was issued."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+")
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")

    description = models.CharField(max_length=200)
    quantity = models.IntegerField()
    unit_price = models.BigIntegerField()
    line_subtotal = models.BigIntegerField()

    #: Recorded, not just the rate: exempt and zero-rated both charge
    #: nothing, but input VAT is reclaimable only on the latter.
    tax_treatment = models.CharField(max_length=10)
    tax_rate_basis_points = models.IntegerField(default=0)
    tax_amount = models.BigIntegerField(default=0)

    class Meta:
        db_table = "commerce_invoice_line"

    def __str__(self) -> str:
        return self.description


class InvoicePayment(TenantModel):
    """Money actually received against an invoice."""

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    amount = models.BigIntegerField()
    method = models.CharField(max_length=20, default="TRANSFER")
    reference = models.CharField(max_length=60, blank=True)
    received_on = models.DateField(default=timezone.localdate)

    class Meta:
        db_table = "commerce_invoice_payment"
        ordering = ["-received_on"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="ck_invoice_payment_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.amount} on {self.invoice}"
