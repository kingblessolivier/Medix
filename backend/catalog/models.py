"""Catalog: products, typed attributes, unit-of-measure chains, registration.

A medicine, a cosmetic, a device and a consumable cannot share one flat
table, so attributes are defined per product type and validated on write.

See docs/03-data-model.md.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from core.models import TenantModel


class ProductTypeCode(models.TextChoices):
    MEDICINE = "MEDICINE", "Medicine"
    COSMETIC = "COSMETIC", "Cosmetic"
    DEVICE = "DEVICE", "Medical device"
    CONSUMABLE = "CONSUMABLE", "Consumable"
    SUPPLEMENT = "SUPPLEMENT", "Supplement"


class LegalStatus(models.TextChoices):
    """Rwanda FDA classification. Drives dispensing rules at point of sale."""

    OTC = "OTC", "Over the counter"
    POM = "POM", "Prescription only"
    CONTROLLED = "CONTROLLED", "Controlled"


class TaxTreatment(models.TextChoices):
    """Exempt is not zero-rated.

    Input VAT on exempt supplies is not reclaimable, which changes true
    cost of goods and therefore margin. Most medicines are exempt in
    Rwanda; cosmetics, devices and consumables generally are not.
    """

    EXEMPT = "EXEMPT", "Exempt"
    STANDARD = "STANDARD", "Standard rated"
    ZERO_RATED = "ZERO", "Zero rated"


class RegistrationStatus(models.TextChoices):
    REGISTERED = "REGISTERED", "Registered"
    SUSPENDED = "SUSPENDED", "Suspended"
    WITHDRAWN = "WITHDRAWN", "Withdrawn"
    NOT_REGISTERED = "NOT_REGISTERED", "Not registered"


class AttributeDataType(models.TextChoices):
    TEXT = "TEXT", "Text"
    NUMBER = "NUMBER", "Number"
    DATE = "DATE", "Date"
    BOOLEAN = "BOOLEAN", "Boolean"
    ENUM = "ENUM", "Enum"


class ProductType(TenantModel):
    code = models.CharField(max_length=20, choices=ProductTypeCode.choices)
    name = models.CharField(max_length=80)

    class Meta:
        db_table = "catalog_product_type"
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], name="uq_product_type"),
        ]

    def __str__(self) -> str:
        return self.name


class AttributeDefinition(TenantModel):
    """What fields a product of this type carries, and how they group.

    ``group`` drives the form sections — Basic, Pharmaceutical, Regulatory,
    Commercial — so a product form is never one flat wall of inputs.
    """

    product_type = models.ForeignKey(
        ProductType, on_delete=models.CASCADE, related_name="attributes"
    )
    code = models.CharField(max_length=40)
    label = models.CharField(max_length=80)
    data_type = models.CharField(max_length=10, choices=AttributeDataType.choices)
    enum_values = models.JSONField(null=True, blank=True)
    required = models.BooleanField(default=False)
    group = models.CharField(max_length=40, default="Basic")
    display_order = models.IntegerField(default=0)

    class Meta:
        db_table = "catalog_attribute_definition"
        ordering = ["group", "display_order", "label"]
        constraints = [
            models.UniqueConstraint(
                fields=["product_type", "code"], name="uq_attribute_code"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product_type.code}.{self.code}"


class Category(TenantModel):
    """Therapeutic category — antibiotic, analgesic, antihypertensive."""

    name = models.CharField(max_length=80)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )

    class Meta:
        db_table = "catalog_category"
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name


class Product(TenantModel):
    product_type = models.ForeignKey(ProductType, on_delete=models.PROTECT, related_name="products")
    name = models.CharField(max_length=200)
    generic_name = models.CharField(max_length=200, blank=True)
    brand = models.CharField(max_length=120, blank=True)
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.PROTECT, related_name="products"
    )

    # Validated on write against this type's AttributeDefinition rows.
    attributes = models.JSONField(default=dict, blank=True)

    legal_status = models.CharField(
        max_length=12, choices=LegalStatus.choices, default=LegalStatus.OTC
    )
    controlled_schedule = models.CharField(max_length=20, blank=True)
    tax_treatment = models.CharField(
        max_length=10, choices=TaxTreatment.choices, default=TaxTreatment.STANDARD
    )
    cold_chain = models.BooleanField(default=False)

    gtin = models.CharField(max_length=14, blank=True, help_text="GS1 (01)")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "catalog_product"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["organization", "name"]),
            models.Index(fields=["organization", "legal_status"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(legal_status="CONTROLLED")
                | ~models.Q(controlled_schedule=""),
                name="ck_controlled_needs_schedule",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def requires_prescription(self) -> bool:
        return self.legal_status in (LegalStatus.POM, LegalStatus.CONTROLLED)

    @property
    def base_uom(self) -> UnitOfMeasure:
        return self.units.get(is_base=True)


class UnitOfMeasure(TenantModel):
    """One level in a product's packaging chain.

    Carton → pack → blister → unit, each with a conversion factor to the
    base unit. All ledger quantities are stored in base units.

    ``is_sellable`` is how a product forbids partial-pack dispensing —
    common for antibiotics dispensed as a full course.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="units")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=60)
    factor_to_base = models.BigIntegerField()
    is_base = models.BooleanField(default=False)
    is_purchase_default = models.BooleanField(default=False)
    is_dispense_default = models.BooleanField(default=False)
    is_sellable = models.BooleanField(default=True)

    class Meta:
        db_table = "catalog_unit_of_measure"
        ordering = ["-factor_to_base"]
        constraints = [
            models.UniqueConstraint(fields=["product", "code"], name="uq_uom_code"),
            models.CheckConstraint(
                condition=models.Q(factor_to_base__gte=1), name="ck_uom_factor_positive"
            ),
            # Exactly one base unit per product, and its factor must be 1.
            models.UniqueConstraint(
                fields=["product"],
                condition=models.Q(is_base=True),
                name="uq_uom_single_base",
            ),
            models.CheckConstraint(
                condition=~models.Q(is_base=True) | models.Q(factor_to_base=1),
                name="ck_uom_base_factor_one",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product_id}:{self.code}"

    def clean(self) -> None:
        if self.is_base and self.factor_to_base != 1:
            raise ValidationError({"factor_to_base": "The base unit must have factor 1."})


class ProductRegistration(TenantModel):
    """Rwanda FDA registration.

    A product whose registration is suspended, withdrawn or expired cannot
    be listed or dispensed; existing stock quarantines rather than staying
    available. See docs/06-compliance.md.
    """

    product = models.OneToOneField(
        Product, on_delete=models.CASCADE, related_name="registration"
    )
    registration_number = models.CharField(max_length=60)
    holder = models.CharField(max_length=200, blank=True)
    local_agent = models.CharField(max_length=200, blank=True)
    strength = models.CharField(max_length=60, blank=True)
    dosage_form = models.CharField(max_length=60, blank=True)
    route = models.CharField(max_length=60, blank=True)
    pack_size = models.CharField(max_length=60, blank=True)
    shelf_life_months = models.IntegerField(null=True, blank=True)
    manufacturer = models.CharField(max_length=200, blank=True)
    manufacturer_country = models.CharField(max_length=80, blank=True)
    registered_on = models.DateField(null=True, blank=True)
    registration_expiry = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=RegistrationStatus.choices, default=RegistrationStatus.REGISTERED
    )

    class Meta:
        db_table = "catalog_product_registration"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "registration_number"], name="uq_registration_number"
            ),
        ]

    def __str__(self) -> str:
        return self.registration_number

    def is_dispensable(self, *, as_of) -> bool:
        if self.status != RegistrationStatus.REGISTERED:
            return False
        return self.registration_expiry is None or self.registration_expiry >= as_of
