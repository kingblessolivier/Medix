"""Sales API shapes."""

from __future__ import annotations

from rest_framework import serializers

from sales.models import (
    ControlledDeliveryEntry,
    Patient,
    Payment,
    Prescription,
    Sale,
    SaleLine,
    Shift,
    Till,
)


class SaleLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    batch_number = serializers.CharField(source="batch.batch_number", read_only=True)
    expiry_date = serializers.DateField(source="batch.expiry_date", read_only=True)
    uom_code = serializers.CharField(source="uom.code", read_only=True)
    requires_prescription = serializers.BooleanField(read_only=True)

    class Meta:
        model = SaleLine
        fields = [
            "id",
            "product",
            "product_name",
            "batch",
            "batch_number",
            "expiry_date",
            "uom_code",
            "quantity",
            "quantity_base",
            "unit_price",
            "line_subtotal",
            "discount",
            "tax_treatment",
            "tax_amount",
            "line_total",
            "legal_status",
            "requires_prescription",
        ]


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            "id",
            "method",
            "provider",
            "amount",
            "currency",
            "status",
            "provider_reference",
            "requested_at",
            "resolved_at",
        ]


class SaleSerializer(serializers.ModelSerializer):
    lines = SaleLineSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    outstanding = serializers.SerializerMethodField()
    blocked_reason = serializers.SerializerMethodField()

    class Meta:
        model = Sale
        fields = [
            "id",
            "number",
            "status",
            "subtotal",
            "tax_total",
            "discount_total",
            "total",
            "currency",
            "outstanding",
            "blocked_reason",
            "occurred_at",
            "completed_at",
            "lines",
            "payments",
        ]

    def get_outstanding(self, obj: Sale) -> int:
        from sales import payments as payment_services

        return payment_services.amount_outstanding(obj)

    def get_blocked_reason(self, obj: Sale) -> str | None:
        """What stops this sale completing, in the words the POS shows.

        Twelve words maximum — see docs/23-ui-copy.md.
        """
        if obj.status != "DRAFT":
            return None
        blocking = [line for line in obj.lines.all() if line.requires_prescription]
        if not blocking:
            return None
        if obj.prescription is None or not obj.prescription.is_verified:
            return f"{blocking[0].product.name} is prescription-only."
        return None


class StartSaleSerializer(serializers.Serializer):
    location = serializers.UUIDField()
    till = serializers.UUIDField(required=False, allow_null=True)


class AddLineSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1)
    uom_code = serializers.CharField(max_length=20)
    unit_price = serializers.IntegerField(min_value=0)
    discount = serializers.IntegerField(min_value=0, default=0)
    batch = serializers.UUIDField(required=False, allow_null=True)
    #: A manual batch choice overrides FEFO and is recorded.
    override_reason = serializers.CharField(max_length=200, required=False, allow_blank=True)


class CompleteSaleSerializer(serializers.Serializer):
    pharmacist = serializers.UUIDField(required=False, allow_null=True)
    prescription = serializers.UUIDField(required=False, allow_null=True)


class TakePaymentSerializer(serializers.Serializer):
    method = serializers.CharField(max_length=16)
    amount = serializers.IntegerField(min_value=1)
    provider_code = serializers.CharField(max_length=20, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)


class PatientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Patient
        fields = ["id", "full_name", "address", "phone"]


class PrescriptionSerializer(serializers.ModelSerializer):
    patient = PatientSerializer(read_only=True)
    is_verified = serializers.BooleanField(read_only=True)

    class Meta:
        model = Prescription
        fields = [
            "id",
            "number",
            "patient",
            "prescriber",
            "issued_on",
            "ocr_extract",
            "status",
            "is_verified",
            "verified_at",
            "verified_by_council_number",
        ]


class TillSerializer(serializers.ModelSerializer):
    class Meta:
        model = Till
        fields = ["id", "name", "code", "branch", "is_active"]


class ShiftSerializer(serializers.ModelSerializer):
    till_name = serializers.CharField(source="till.name", read_only=True)

    class Meta:
        model = Shift
        fields = [
            "id",
            "till",
            "till_name",
            "status",
            "opening_float",
            "counted_cash",
            "variance",
            "variance_reason",
            "opened_at",
            "closed_at",
        ]


class DayEndSerializer(serializers.Serializer):
    sales_total = serializers.IntegerField()
    transactions = serializers.IntegerField()
    items_sold = serializers.IntegerField()
    discounts = serializers.IntegerField()
    tax_total = serializers.IntegerField()
    by_method = serializers.DictField(child=serializers.IntegerField())
    expected_cash = serializers.IntegerField()
    counted_cash = serializers.IntegerField(allow_null=True)
    variance = serializers.IntegerField(allow_null=True)
    pending_payments = serializers.IntegerField()


class CloseShiftSerializer(serializers.Serializer):
    counted_cash = serializers.IntegerField(min_value=0)
    variance_reason = serializers.CharField(max_length=500, required=False, allow_blank=True)
    allow_pending = serializers.BooleanField(default=False)


class ControlledEntrySerializer(serializers.ModelSerializer):
    """The statutory register, as it is reported."""

    dispensed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ControlledDeliveryEntry
        fields = [
            "id",
            "patient_name",
            "patient_address",
            "substance_denomination",
            "schedule",
            "quantity_base",
            "uom_code",
            "dispensed_by_name",
            "dispensed_by_council_number",
            "balance_after_base",
            "entered_at",
        ]

    def get_dispensed_by_name(self, obj) -> str:
        return str(obj.dispensed_by)
