"""Catalog API shapes."""

from __future__ import annotations

from rest_framework import serializers

from catalog.models import (
    AttributeDefinition,
    Category,
    Product,
    ProductRegistration,
    ProductType,
    UnitOfMeasure,
)
from catalog.services import validate_attributes


class UnitOfMeasureSerializer(serializers.ModelSerializer):
    class Meta:
        model = UnitOfMeasure
        fields = [
            "id",
            "code",
            "name",
            "factor_to_base",
            "is_base",
            "is_purchase_default",
            "is_dispense_default",
            "is_sellable",
        ]


class AttributeDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttributeDefinition
        fields = [
            "id",
            "code",
            "label",
            "data_type",
            "enum_values",
            "required",
            "group",
            "display_order",
        ]


class ProductTypeSerializer(serializers.ModelSerializer):
    attributes = AttributeDefinitionSerializer(many=True, read_only=True)

    class Meta:
        model = ProductType
        fields = ["id", "code", "name", "attributes"]


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "parent"]


class ProductRegistrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductRegistration
        fields = [
            "id",
            "registration_number",
            "holder",
            "local_agent",
            "strength",
            "dosage_form",
            "route",
            "pack_size",
            "manufacturer",
            "manufacturer_country",
            "registered_on",
            "registration_expiry",
            "status",
        ]


class ProductListSerializer(serializers.ModelSerializer):
    """List shape — deliberately narrow. The table shows these columns."""

    product_type_code = serializers.CharField(source="product_type.code", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)
    requires_prescription = serializers.BooleanField(read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "generic_name",
            "brand",
            "product_type_code",
            "category_name",
            "legal_status",
            "requires_prescription",
            "tax_treatment",
            "cold_chain",
            "gtin",
            "is_active",
        ]


class ProductDetailSerializer(serializers.ModelSerializer):
    units = UnitOfMeasureSerializer(many=True, read_only=True)
    registration = ProductRegistrationSerializer(read_only=True)
    product_type = ProductTypeSerializer(read_only=True)
    category = CategorySerializer(read_only=True)
    requires_prescription = serializers.BooleanField(read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "generic_name",
            "brand",
            "product_type",
            "category",
            "attributes",
            "legal_status",
            "controlled_schedule",
            "requires_prescription",
            "tax_treatment",
            "cold_chain",
            "gtin",
            "is_active",
            "units",
            "registration",
            "created_at",
            "modified_at",
        ]


class ProductWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = [
            "name",
            "generic_name",
            "brand",
            "product_type",
            "category",
            "attributes",
            "legal_status",
            "controlled_schedule",
            "tax_treatment",
            "cold_chain",
            "gtin",
            "is_active",
        ]

    def validate(self, data: dict) -> dict:
        product_type = data.get("product_type") or getattr(self.instance, "product_type", None)
        attributes = data.get("attributes")
        if product_type is not None and attributes is not None:
            # Raises DomainError, which the exception handler maps to 422.
            validate_attributes(product_type, attributes)

        legal_status = data.get("legal_status") or getattr(self.instance, "legal_status", None)
        schedule = data.get("controlled_schedule", getattr(self.instance, "controlled_schedule", ""))
        if legal_status == "CONTROLLED" and not schedule:
            raise serializers.ValidationError(
                {"controlled_schedule": "Required for a controlled product."}
            )
        return data
