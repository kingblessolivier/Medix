"""Inventory API shapes.

The ledger is read-only over HTTP. Movements are created only as a side
effect of a domain action, enforced at the service layer.
"""

from __future__ import annotations

from rest_framework import serializers

from core.fields import MoneyField
from inventory.models import Batch, Location, StockBalance, StockMovement


class LocationSerializer(serializers.ModelSerializer):
    is_cold_capable = serializers.BooleanField(read_only=True)

    class Meta:
        model = Location
        fields = [
            "id",
            "name",
            "code",
            "kind",
            "parent",
            "temperature_class",
            "is_cold_capable",
            "is_active",
        ]


class BatchSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True, default=None)
    unit_cost = MoneyField(source="unit_cost_base", read_only=True)

    class Meta:
        model = Batch
        fields = [
            "id",
            "batch_number",
            "product",
            "product_name",
            "supplier",
            "supplier_name",
            "manufacture_date",
            "expiry_date",
            "unit_cost",
            "gtin",
            "serial",
            "cold_chain",
        ]


class StockBalanceSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    batch_number = serializers.CharField(source="batch.batch_number", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)
    days_to_expiry = serializers.SerializerMethodField()

    class Meta:
        model = StockBalance
        fields = [
            "id",
            "product",
            "product_name",
            "batch",
            "batch_number",
            "location",
            "location_name",
            "status",
            "quantity_base",
            "expiry_date",
            "days_to_expiry",
            "updated_at",
        ]

    def get_days_to_expiry(self, obj: StockBalance) -> int:
        from django.utils import timezone

        return (obj.expiry_date - timezone.localdate()).days


class StockMovementSerializer(serializers.ModelSerializer):
    """Read-only. There is no POST on this resource.

    Movements are created only as a side effect of a domain action —
    receiving, a sale, a transfer, an adjustment.
    """

    product_name = serializers.CharField(source="product.name", read_only=True)
    batch_number = serializers.CharField(source="batch.batch_number", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)
    performed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "kind",
            "product",
            "product_name",
            "batch",
            "batch_number",
            "location",
            "location_name",
            "status",
            "quantity_base",
            "balance_after_base",
            "reason",
            "reference",
            "performed_by_name",
            "occurred_at",
            "recorded_at",
        ]

    def get_performed_by_name(self, obj: StockMovement) -> str | None:
        return str(obj.performed_by) if obj.performed_by_id else None


class AdjustmentInputSerializer(serializers.Serializer):
    """A stock adjustment always carries a reason. That is the point of it."""

    batch = serializers.UUIDField()
    location = serializers.UUIDField()
    quantity = serializers.IntegerField()
    uom_code = serializers.CharField(max_length=20)
    reason = serializers.CharField(max_length=500)

    def validate_quantity(self, value: int) -> int:
        if value == 0:
            raise serializers.ValidationError("Quantity cannot be zero.")
        return value

    def validate_reason(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("A reason is required for an adjustment.")
        return value


class ReceiptInputSerializer(serializers.Serializer):
    """Receive stock against a batch."""

    batch = serializers.UUIDField()
    location = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1)
    uom_code = serializers.CharField(max_length=20)
    reference = serializers.CharField(max_length=60, required=False, allow_blank=True)


class AllocationPreviewInputSerializer(serializers.Serializer):
    """Ask which batches FEFO would pick, without committing."""

    product = serializers.UUIDField()
    location = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1)
    uom_code = serializers.CharField(max_length=20)


class AllocationSerializer(serializers.Serializer):
    batch_id = serializers.UUIDField()
    batch_number = serializers.CharField()
    expiry_date = serializers.DateField()
    quantity_base = serializers.IntegerField()


class TransferSerializer(serializers.Serializer):
    """Stock between two locations of one organization."""

    batch = serializers.UUIDField()
    from_location = serializers.UUIDField()
    to_location = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1)
    uom_code = serializers.CharField(max_length=20, required=False, allow_blank=True)
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class QuarantineSerializer(serializers.Serializer):
    """A hold, not a removal. The reason is what makes it reviewable."""

    batch = serializers.UUIDField()
    location = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1)
    uom_code = serializers.CharField(max_length=20, required=False, allow_blank=True)
    reason = serializers.CharField(max_length=500)


class SupplierReturnSerializer(serializers.Serializer):
    batch = serializers.UUIDField()
    location = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1)
    uom_code = serializers.CharField(max_length=20, required=False, allow_blank=True)
    reason = serializers.CharField(max_length=500)
    #: Usually QUARANTINED — the common case is sending back something
    #: that was held on arrival.
    status = serializers.CharField(max_length=12, required=False, allow_blank=True)


class RecallSerializer(serializers.Serializer):
    batch = serializers.UUIDField()
    reason = serializers.CharField(max_length=500)
    authority_reference = serializers.CharField(
        max_length=60, required=False, allow_blank=True
    )
