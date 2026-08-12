"""Commerce API shapes."""

from __future__ import annotations

from rest_framework import serializers

from commerce.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    Shipment,
    ShipmentLine,
    TradingRelationship,
    VendorListing,
)


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

    # Read from the queryset annotation. A declared field with both
    # read_only and a default makes DRF skip the attribute lookup
    # entirely, which silently returned zero stock for every listing.
    stock_base = serializers.SerializerMethodField()
    earliest_expiry = serializers.SerializerMethodField()

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
            "stock_base",
            "earliest_expiry",
        ]

    vendor = serializers.UUIDField(source="organization_id", read_only=True)

    def get_stock_base(self, obj) -> int:
        """Zero when the queryset did not annotate it — a vendor's own
        listing view does not need the number."""
        return getattr(obj, "stock_base", 0) or 0

    def get_earliest_expiry(self, obj):
        return getattr(obj, "earliest_expiry", None)


class VendorComparisonSerializer(serializers.Serializer):
    """Price against expiry, MOQ and lead time — the whole tradeoff.

    The cheapest row is frequently the wrong choice, so every dimension a
    buyer weighs is present.
    """

    listing_id = serializers.UUIDField()
    vendor_name = serializers.CharField()
    price = serializers.IntegerField()
    uom = serializers.CharField()
    availability = serializers.CharField()
    stock_base = serializers.IntegerField()
    earliest_expiry = serializers.DateField(allow_null=True)
    moq = serializers.IntegerField()
    lead_time_days = serializers.IntegerField()


class PublishListingSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    price = serializers.IntegerField(min_value=0)
    uom_code = serializers.CharField(max_length=20)
    availability = serializers.CharField(max_length=20, required=False)
    moq = serializers.IntegerField(min_value=1, default=1)
    lead_time_days = serializers.IntegerField(min_value=0, default=1)


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


class PurchaseOrderSerializer(serializers.ModelSerializer):
    lines = PurchaseOrderLineSerializer(many=True, read_only=True)
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
            "submitted_at",
            "confirmed_at",
            "created_at",
            "lines",
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
            "dispatched_at",
            "lines",
        ]


class DispatchSerializer(serializers.Serializer):
    from_location = serializers.UUIDField()
    carrier = serializers.CharField(max_length=120, required=False, allow_blank=True)


class StartOrderSerializer(serializers.Serializer):
    supplier = serializers.UUIDField()
    deliver_to = serializers.UUIDField()
    required_by = serializers.DateField(required=False, allow_null=True)


class AddOrderLineSerializer(serializers.Serializer):
    listing = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1)


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
            "gtin",
        ]


class GoodsReceiptSerializer(serializers.ModelSerializer):
    lines = GoodsReceiptLineSerializer(many=True, read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True, default=None)
    location_name = serializers.CharField(source="location.name", read_only=True)
    has_discrepancy = serializers.BooleanField(read_only=True)

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
            "lines",
        ]


class StartReceiptSerializer(serializers.Serializer):
    location = serializers.UUIDField()
    order = serializers.UUIDField(required=False, allow_null=True)
    supplier = serializers.UUIDField(required=False, allow_null=True)


class AddReceiptLineSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    uom_code = serializers.CharField(max_length=20)
    received = serializers.IntegerField(min_value=0)
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
