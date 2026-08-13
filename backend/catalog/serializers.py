"""Catalog API shapes."""

from __future__ import annotations

from rest_framework import serializers

from catalog.models import (
    AttributeDefinition,
    Category,
    ClinicalAttribute,
    Manufacturer,
    Product,
    ProductImage,
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

    #: What is on the shelf, in base units. Annotated by the viewset, so
    #: the list stays one query.
    #
    # On the row because the till reads this list: a cashier searching
    # "paracetamol" gets several products that look alike, and finding out
    # which one is sellable by tapping it and reading a red banner is a
    # conversation with the patient that should not have to happen.
    on_hand_base = serializers.IntegerField(read_only=True, default=0)
    base_uom_name = serializers.SerializerMethodField()

    def get_base_uom_name(self, product) -> str:
        """Names the unit the figure is in. "0" is not an answer."""
        for unit in product.units.all():
            if unit.is_base:
                return unit.name
        return ""

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
            "on_hand_base",
            "base_uom_name",
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


class ScanInputSerializer(serializers.Serializer):
    """The raw string a scanner emits, control characters and all."""

    code = serializers.CharField(max_length=200, trim_whitespace=False)


class ScanResultSerializer(serializers.Serializer):
    """What the barcode carried, and what it matched.

    Fields the barcode did not carry come back null. A scan never invents
    data.
    """

    gtin = serializers.CharField(allow_null=True)
    batch_number = serializers.CharField(allow_null=True)
    expiry_date = serializers.DateField(allow_null=True)
    serial = serializers.CharField(allow_null=True)
    matched = serializers.BooleanField()
    product = ProductListSerializer(allow_null=True)
    batch = serializers.SerializerMethodField()

    def get_batch(self, obj) -> dict | None:
        batch = obj.get("batch")
        if batch is None:
            return None
        return {
            "id": str(batch.id),
            "batch_number": batch.batch_number,
            "expiry_date": batch.expiry_date,
        }


class ManufacturerSerializer(serializers.ModelSerializer):
    """Who made it, and where.

    `gmp_certified` is a purchasing fact, not a label — a depot may be
    barred from importing from an uncertified site — so it is filterable
    rather than buried in a note.
    """

    product_count = serializers.SerializerMethodField()

    class Meta:
        model = Manufacturer
        fields = [
            "id",
            "name",
            "country_of_origin",
            "gmp_certified",
            "gmp_expiry",
            "is_active",
            "product_count",
        ]

    def get_product_count(self, manufacturer) -> int:
        return getattr(manufacturer, "product_count", 0)


class UnitOfMeasureWriteSerializer(serializers.ModelSerializer):
    """One rung of a packaging chain.

    Validated as a chain rather than a row — exactly one base at factor 1
    and no two levels sharing a factor — because every ledger quantity is
    stored in base units and a broken chain corrupts them silently.
    """

    class Meta:
        model = UnitOfMeasure
        fields = [
            "id",
            "product",
            "code",
            "name",
            "factor_to_base",
            "is_base",
            "is_purchase_default",
            "is_dispense_default",
            "is_sellable",
        ]


class ProductImageSerializer(serializers.ModelSerializer):
    """A picture of the actual pack.

    `alt` is required, not optional: the image is how a buyer verifies the
    presentation before committing to a carton, and a product whose image
    a screen reader cannot describe is a product one buyer cannot check.
    """

    class Meta:
        model = ProductImage
        fields = ["id", "product", "image", "alt", "is_primary", "position"]


class ClinicalAttributeSerializer(serializers.ModelSerializer):
    """A sourced clinical fact, with the dates it applied.

    `source` is required by a database constraint as well as here. A
    clinical threshold with no cited origin is an opinion, and this system
    does not hold opinions about medicines.
    """

    kind_label = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = ClinicalAttribute
        fields = [
            "id",
            "product",
            "kind",
            "kind_label",
            "value_number",
            "value_text",
            "source",
            "source_reference",
            "effective_from",
            "effective_to",
        ]
