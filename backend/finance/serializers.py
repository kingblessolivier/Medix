"""Finance API shapes."""

from __future__ import annotations

from rest_framework import serializers

from finance.models import Expense, ExpenseCategory, WriteOff, WriteOffReason


class ExpenseCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseCategory
        fields = ["id", "code", "name", "is_operating", "is_active"]


class ExpenseSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = Expense
        fields = [
            "id",
            "category",
            "category_name",
            "branch",
            "incurred_on",
            "amount",
            "currency",
            "description",
            "payee",
            "reference",
        ]


class WriteOffSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="batch.product.name", read_only=True)
    batch_number = serializers.CharField(source="batch.batch_number", read_only=True)
    reason_label = serializers.CharField(source="get_reason_display", read_only=True)

    class Meta:
        model = WriteOff
        fields = [
            "id",
            "number",
            "batch",
            "batch_number",
            "product_name",
            "location",
            "reason",
            "reason_label",
            "quantity_base",
            "unit_cost_base",
            "value",
            "currency",
            "written_off_on",
            "witness_name",
            "witness_role",
        ]


class WriteOffRequestSerializer(serializers.Serializer):
    batch = serializers.UUIDField()
    location = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1)
    uom_code = serializers.CharField(max_length=20, required=False, allow_blank=True)
    reason = serializers.ChoiceField(choices=WriteOffReason.choices)
    witness_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    witness_role = serializers.CharField(max_length=80, required=False, allow_blank=True)
    written_off_on = serializers.DateField(required=False)


class PeriodQuerySerializer(serializers.Serializer):
    """The range is required in both directions.

    A defaulted start date would quietly answer a different question from
    the one asked, and a report whose period is implicit is a report
    nobody can check.
    """

    start = serializers.DateField()
    end = serializers.DateField()
    tier = serializers.ChoiceField(choices=["DEPOT", "RETAIL"], default="RETAIL")
