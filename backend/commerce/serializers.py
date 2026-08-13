"""Commerce API shapes."""

from __future__ import annotations

from rest_framework import serializers

from commerce.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    ImportDocument,
    OrderEvent,
    PriceTier,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    Shipment,
    ShipmentLine,
    TradingRelationship,
    VendorListing,
)


class PriceTierSerializer(serializers.ModelSerializer):
    class Meta:
        model = PriceTier
        fields = ["min_quantity", "price"]


class MarketplaceRowSerializer(serializers.ModelSerializer):
    """One offer in the browse list.

    Deliberately narrow — these are the columns the table shows and the
    fields a small card needs. Nothing more.
    """

    product_name = serializers.CharField(source="product.name", read_only=True)
    generic_name = serializers.CharField(source="product.generic_name", read_only=True)
    vendor_name = serializers.CharField(source="organization.name", read_only=True)
    uom_code = serializers.CharField(source="price_uom.code", read_only=True)
    uom_name = serializers.CharField(source="price_uom.name", read_only=True)
    legal_status = serializers.CharField(source="product.legal_status", read_only=True)
    requires_prescription = serializers.BooleanField(
        source="product.requires_prescription", read_only=True
    )
    cold_chain = serializers.BooleanField(source="product.cold_chain", read_only=True)
    is_orderable = serializers.BooleanField(read_only=True)

    # Classification. A buyer's first question is "show me the
    # antibiotics", not "show me everything alphabetically", so the
    # therapeutic category and the product type travel with every row.
    category_name = serializers.CharField(
        source="product.category.name", read_only=True, default=None
    )
    product_type_code = serializers.CharField(
        source="product.product_type.code", read_only=True
    )
    brand = serializers.CharField(source="product.brand", read_only=True)
    dosage_form = serializers.SerializerMethodField()

    # What the depot published, less what is already committed. Named
    # `available_base` rather than `stock_base` because it is not the
    # stock: a depot holding 500 packs may be offering 200.
    available_base = serializers.IntegerField(read_only=True)
    earliest_expiry = serializers.SerializerMethodField()

    units = serializers.SerializerMethodField()

    def get_units(self, listing) -> list[dict]:
        """Levels this depot will sell at, priced.

        Derived from the listing price rather than stored, so a repricing
        cannot leave a stale per-unit figure behind. Largest first — a
        buyer scans down from the carton.
        """
        from core import pricing
        from core.money import Money

        out = []
        for unit in sorted(
            listing.product.units.all(), key=lambda u: -u.factor_to_base
        ):
            if not unit.is_sellable:
                continue
            price = (
                listing.price
                if unit.id == listing.price_uom_id
                else pricing.derive(
                    Money(listing.price, listing.currency),
                    from_uom=listing.price_uom,
                    to_uom=unit,
                ).price.amount
            )
            out.append(
                {
                    "code": unit.code,
                    "name": unit.name,
                    "factor_to_base": unit.factor_to_base,
                    "price": price,
                    "is_priced": unit.id == listing.price_uom_id,
                }
            )
        return out

    def get_dosage_form(self, listing) -> str:
        """The base unit is the form — tablet, bottle, vial, pair."""
        registration = getattr(listing.product, "registration", None)
        if registration and registration.dosage_form:
            return registration.dosage_form
        base = listing.product.units.filter(is_base=True).first()
        return base.name if base else ""

    class Meta:
        model = VendorListing
        fields = [
            "id",
            "product",
            "product_name",
            "generic_name",
            "brand",
            "category_name",
            "product_type_code",
            "dosage_form",
            "units",
            "vendor",
            "vendor_name",
            "availability",
            "is_orderable",
            "price",
            "currency",
            "uom_code",
            "uom_name",
            "moq",
            "lead_time_days",
            "legal_status",
            "requires_prescription",
            "cold_chain",
            "available_base",
            "earliest_expiry",
            "srp",
            "tiers",
        ]

    vendor = serializers.UUIDField(source="organization_id", read_only=True)
    tiers = PriceTierSerializer(many=True, read_only=True)

    def get_earliest_expiry(self, obj):
        return getattr(obj, "earliest_expiry", None)


class PublishListingSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    price = serializers.IntegerField(min_value=0)
    uom_code = serializers.CharField(max_length=20)
    availability = serializers.CharField(max_length=20, required=False)
    moq = serializers.IntegerField(min_value=1, default=1)
    lead_time_days = serializers.IntegerField(min_value=0, default=1)
    srp = serializers.IntegerField(min_value=0, required=False, allow_null=True)


class SetPriceTiersSerializer(serializers.Serializer):
    """Volume breaks, replacing whatever the listing had.

    Replace rather than append: a depot editing its breaks is restating
    the whole schedule, and merging would leave a threshold nobody
    remembers setting still quietly in force.
    """

    tiers = PriceTierSerializer(many=True)


class PurchaseOrderLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    uom_code = serializers.CharField(source="uom.code", read_only=True)
    outstanding_base = serializers.IntegerField(read_only=True)
    undispatched_base = serializers.IntegerField(read_only=True)

    class Meta:
        model = PurchaseOrderLine
        fields = [
            "id",
            "product",
            "product_name",
            "uom_code",
            "quantity",
            "quantity_base",
            "unit_price",
            "line_total",
            "received_base",
            "outstanding_base",
            "dispatched_base",
            "undispatched_base",
        ]


class OrderEventSerializer(serializers.ModelSerializer):
    """One step of the timeline, as both sides see it.

    The actor's name is exposed but never their username or address —
    the counterparty is entitled to know who moved the order, not to
    profile the other pharmacy's staff.
    """

    actor_name = serializers.SerializerMethodField()
    actor_organization_name = serializers.CharField(
        source="actor_organization.name", read_only=True, default=""
    )
    to_status_label = serializers.SerializerMethodField()

    class Meta:
        model = OrderEvent
        fields = [
            "id",
            "from_status",
            "to_status",
            "to_status_label",
            "actor_name",
            "actor_organization",
            "actor_organization_name",
            "occurred_at",
            "note",
            "document_number",
        ]

    def get_actor_name(self, event) -> str:
        return str(event.actor) if event.actor_id else ""

    def get_to_status_label(self, event) -> str:
        return PurchaseOrderStatus(event.to_status).label


class PurchaseOrderSerializer(serializers.ModelSerializer):
    lines = PurchaseOrderLineSerializer(many=True, read_only=True)
    events = OrderEventSerializer(many=True, read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    buyer_name = serializers.CharField(source="organization.name", read_only=True)
    deliver_to_name = serializers.CharField(source="deliver_to.name", read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "id",
            "number",
            "status",
            "supplier",
            "supplier_name",
            "buyer_name",
            "deliver_to",
            "deliver_to_name",
            "required_by",
            "subtotal",
            "currency",
            "payment_terms_days",
            "submitted_at",
            "confirmed_at",
            "created_at",
            "lines",
            "events",
        ]


class ShipmentLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    uom_code = serializers.CharField(source="uom.code", read_only=True)

    class Meta:
        model = ShipmentLine
        fields = [
            "id",
            "order_line",
            "product",
            "product_name",
            "uom_code",
            "quantity_base",
            "batch_number",
            "expiry_date",
        ]


class ShipmentSerializer(serializers.ModelSerializer):
    """The delivery note. What the receiver checks the cartons against."""

    lines = ShipmentLineSerializer(many=True, read_only=True)
    order_number = serializers.CharField(source="order.number", read_only=True)
    from_location_name = serializers.CharField(source="from_location.name", read_only=True)

    class Meta:
        model = Shipment
        fields = [
            "id",
            "number",
            "status",
            "order",
            "order_number",
            "from_location",
            "from_location_name",
            "carrier",
            "vehicle_registration",
            "driver_name",
            "driver_licence",
            "received_by_name",
            "received_by_registration",
            "received_at",
            "dispatched_at",
            "lines",
        ]


class DispatchSerializer(serializers.Serializer):
    from_location = serializers.UUIDField()
    carrier = serializers.CharField(max_length=120, required=False, allow_blank=True)
    vehicle_registration = serializers.CharField(
        max_length=20, required=False, allow_blank=True
    )
    driver_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    driver_licence = serializers.CharField(max_length=40, required=False, allow_blank=True)
    #: The releasing pharmacist's registration. Required only when a line
    #: is controlled; the service is what enforces that, not this shape.
    controlled_transfer = serializers.UUIDField(required=False, allow_null=True)


class StartOrderSerializer(serializers.Serializer):
    supplier = serializers.UUIDField()
    deliver_to = serializers.UUIDField()
    required_by = serializers.DateField(required=False, allow_null=True)


class SellableUnitSerializer(serializers.Serializer):
    """A level the depot will actually sell at, with its derived price."""

    code = serializers.CharField()
    name = serializers.CharField()
    factor_to_base = serializers.IntegerField()
    price = serializers.IntegerField()
    is_priced = serializers.BooleanField()


class AddOrderLineSerializer(serializers.Serializer):
    listing = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1)
    #: Any level the depot sells at. Defaults to the listing's own unit.
    uom_code = serializers.CharField(max_length=20, required=False, allow_blank=True)


class GoodsReceiptLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    uom_code = serializers.CharField(source="uom.code", read_only=True)
    is_short = serializers.BooleanField(read_only=True)

    class Meta:
        model = GoodsReceiptLine
        fields = [
            "id",
            "product",
            "product_name",
            "uom_code",
            "ordered",
            "received",
            "accepted",
            "rejected",
            "rejection_reason",
            "is_short",
            "batch_number",
            "expiry_date",
            "unit_cost_base",
            "landed_cost_share",
            "gtin",
        ]


class GoodsReceiptSerializer(serializers.ModelSerializer):
    lines = GoodsReceiptLineSerializer(many=True, read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True, default=None)
    location_name = serializers.CharField(source="location.name", read_only=True)
    has_discrepancy = serializers.BooleanField(read_only=True)
    landed_charges = serializers.IntegerField(read_only=True)

    class Meta:
        model = GoodsReceipt
        fields = [
            "id",
            "number",
            "status",
            "order",
            "supplier",
            "supplier_name",
            "location",
            "location_name",
            "received_on",
            "posted_at",
            "transport_temperature_ok",
            "has_discrepancy",
            "invoice_number",
            "invoice_currency",
            "fx_rate_scaled",
            "fx_rate_date",
            "fx_rate_is_official",
            "freight",
            "customs_duty",
            "clearing_fees",
            "other_charges",
            "landed_charges",
            "lines",
        ]


class AcknowledgeSerializer(serializers.Serializer):
    """Warnings the caller has seen and accepted.

    Codes, not a boolean. "I accepted the warnings" is unfalsifiable once
    a new warning appears; naming them means a check added tomorrow is
    not silently pre-accepted by yesterday's client.
    """

    acknowledged = serializers.ListField(
        child=serializers.CharField(max_length=60), required=False, default=list
    )
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class ImportTransferSerializer(serializers.Serializer):
    """A supplier's advance notice, arriving as a file.

    The payload is taken as an opaque dict and validated by
    `commerce.payloads` against its schema version — restating the shape
    here would give two definitions of it that could disagree.
    """

    payload = serializers.JSONField()
    location = serializers.UUIDField()
    order = serializers.UUIDField(required=False, allow_null=True)


class StartReceiptSerializer(serializers.Serializer):
    location = serializers.UUIDField()
    order = serializers.UUIDField(required=False, allow_null=True)
    supplier = serializers.UUIDField(required=False, allow_null=True)


class QuantityEntrySerializer(serializers.Serializer):
    """One rung of a mixed-unit count."""

    uom_code = serializers.CharField(max_length=20)
    count = serializers.IntegerField(min_value=0)


class LandedCostSerializer(serializers.Serializer):
    invoice_number = serializers.CharField(max_length=60, required=False, allow_blank=True)
    invoice_currency = serializers.CharField(max_length=3, required=False)
    fx_rate_scaled = serializers.IntegerField(min_value=1, required=False)
    fx_rate_date = serializers.DateField(required=False, allow_null=True)
    fx_rate_is_official = serializers.BooleanField(required=False)
    freight = serializers.IntegerField(min_value=0, required=False)
    customs_duty = serializers.IntegerField(min_value=0, required=False)
    clearing_fees = serializers.IntegerField(min_value=0, required=False)
    other_charges = serializers.IntegerField(min_value=0, required=False)


class AddReceiptLineSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    uom_code = serializers.CharField(max_length=20)
    received = serializers.IntegerField(min_value=0, required=False)
    #: Instead of `received`: a count across several levels at once.
    entries = QuantityEntrySerializer(many=True, required=False)
    accepted = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    rejected = serializers.IntegerField(min_value=0, default=0)
    rejection_reason = serializers.CharField(max_length=200, required=False, allow_blank=True)
    batch_number = serializers.CharField(max_length=60)
    expiry_date = serializers.DateField()
    unit_cost_base = serializers.IntegerField(min_value=0, default=0)
    order_line = serializers.UUIDField(required=False, allow_null=True)
    gtin = serializers.CharField(max_length=14, required=False, allow_blank=True)
    serial = serializers.CharField(max_length=40, required=False, allow_blank=True)


class TradingRelationshipSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)

    class Meta:
        model = TradingRelationship
        fields = [
            "id",
            "customer",
            "customer_name",
            "credit_limit",
            "payment_terms_days",
            "is_verified",
            "verified_at",
            "is_active",
        ]


class ImportDocumentSerializer(serializers.ModelSerializer):
    """Importation paper, filed against what it covers."""

    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    is_verified = serializers.BooleanField(read_only=True)
    batch_number = serializers.CharField(
        source="batch.batch_number", read_only=True, default=""
    )

    class Meta:
        model = ImportDocument
        fields = [
            "id",
            "kind",
            "kind_label",
            "receipt",
            "batch",
            "batch_number",
            "number",
            "issued_by",
            "issued_on",
            "expires_on",
            "file",
            "is_verified",
            "verified_at",
            "min_temperature_c",
            "max_temperature_c",
            "breach",
        ]
        read_only_fields = ["verified_at"]


class ReleaseBatchSerializer(serializers.Serializer):
    """Quarantined stock, moved to available with a recorded reason.

    The reason is required. A release nobody has to justify is not a
    control, and quarantine only means something if leaving it costs a
    sentence.
    """

    batch = serializers.UUIDField()
    location = serializers.UUIDField()
    reason = serializers.CharField(max_length=500)
