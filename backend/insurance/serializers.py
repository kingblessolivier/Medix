"""Insurance API shapes."""

from __future__ import annotations

from rest_framework import serializers

from insurance.models import (
    CapitationReceipt,
    Claim,
    ClaimLine,
    ClaimPayment,
    CoverageRule,
    Member,
    Scheme,
    SchemeContract,
)


class SchemeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Scheme
        fields = [
            "id",
            "name",
            "code",
            "contact_name",
            "contact_phone",
            "contact_email",
            "is_active",
        ]


class SchemeContractSerializer(serializers.ModelSerializer):
    scheme_name = serializers.CharField(source="scheme.name", read_only=True)
    model_label = serializers.CharField(source="get_model_display", read_only=True)
    claims_per_sale = serializers.BooleanField(read_only=True)

    class Meta:
        model = SchemeContract
        fields = [
            "id",
            "scheme",
            "scheme_name",
            "reference",
            "model",
            "model_label",
            "claims_per_sale",
            "is_contracted",
            "claim_window_days",
            "payment_terms_days",
            "capitation_amount",
            "capitation_period",
            "effective_from",
            "effective_to",
        ]


class CoverageRuleSerializer(serializers.ModelSerializer):
    scope_label = serializers.CharField(source="get_scope_display", read_only=True)
    product_name = serializers.CharField(
        source="product.name", read_only=True, default=""
    )
    category_name = serializers.CharField(
        source="category.name", read_only=True, default=""
    )

    class Meta:
        model = CoverageRule
        fields = [
            "id",
            "contract",
            "scope",
            "scope_label",
            "product",
            "product_name",
            "category",
            "category_name",
            "legal_status",
            "coverage_basis_points",
            "maximum_amount",
            "is_excluded",
            "requires_prescription",
            "effective_from",
            "effective_to",
        ]


class MemberSerializer(serializers.ModelSerializer):
    scheme_name = serializers.CharField(source="scheme.name", read_only=True)
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    is_currently_valid = serializers.SerializerMethodField()

    class Meta:
        model = Member
        fields = [
            "id",
            "patient",
            "patient_name",
            "scheme",
            "scheme_name",
            "member_number",
            "principal_name",
            "valid_from",
            "valid_to",
            "is_active",
            "is_currently_valid",
        ]

    def get_is_currently_valid(self, member) -> bool:
        return member.is_valid()


class ClaimLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(
        source="sale_line.product.name", read_only=True
    )

    class Meta:
        model = ClaimLine
        fields = [
            "id",
            "sale_line",
            "product_name",
            "gross_amount",
            "covered_amount",
            "patient_amount",
            "coverage_basis_points",
            "allowed_amount",
            "is_rejected",
            "rejection_reason",
        ]


class ClaimPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClaimPayment
        fields = ["id", "amount", "received_on", "remittance_reference"]


class ClaimSerializer(serializers.ModelSerializer):
    scheme_name = serializers.CharField(source="scheme.name", read_only=True)
    member_number = serializers.CharField(source="member.member_number", read_only=True)
    patient_name = serializers.CharField(
        source="member.patient.full_name", read_only=True
    )
    sale_number = serializers.CharField(source="sale.number", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    outstanding = serializers.IntegerField(read_only=True)
    settled = serializers.IntegerField(read_only=True)
    lines = ClaimLineSerializer(many=True, read_only=True)
    payments = ClaimPaymentSerializer(many=True, read_only=True)

    class Meta:
        model = Claim
        fields = [
            "id",
            "number",
            "scheme",
            "scheme_name",
            "member_number",
            "patient_name",
            "sale",
            "sale_number",
            "status",
            "status_label",
            "claimed_amount",
            "allowed_amount",
            "patient_paid",
            "settled",
            "outstanding",
            "currency",
            "dispensed_on",
            "submit_by",
            "submitted_at",
            "responded_at",
            "rejection_reason",
            "scheme_reference",
            "lines",
            "payments",
        ]


class ClaimResponseSerializer(serializers.Serializer):
    """What the scheme allowed, line by line.

    Per line because a partial rejection is the common case, and knowing
    *which* line was refused is what makes it fixable.
    """

    allowed = serializers.DictField(child=serializers.IntegerField(), required=False)
    rejections = serializers.DictField(child=serializers.CharField(), required=False)
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    scheme_reference = serializers.CharField(
        max_length=60, required=False, allow_blank=True, default=""
    )


class ClaimPaymentRequestSerializer(serializers.Serializer):
    amount = serializers.IntegerField(min_value=1)
    received_on = serializers.DateField(required=False)
    remittance_reference = serializers.CharField(
        max_length=60, required=False, allow_blank=True, default=""
    )


class CapitationReceiptSerializer(serializers.ModelSerializer):
    scheme_name = serializers.CharField(source="contract.scheme.name", read_only=True)

    class Meta:
        model = CapitationReceipt
        fields = [
            "id",
            "contract",
            "scheme_name",
            "period_start",
            "period_end",
            "members_covered",
            "amount",
            "received_on",
            "remittance_reference",
        ]
