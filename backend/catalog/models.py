"""Catalog: products, typed attributes, unit-of-measure chains, registration.

A medicine, a cosmetic, a device and a consumable cannot share one flat
table, so attributes are defined per product type and validated on write.

See docs/03-data-model.md.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

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


class Manufacturer(TenantModel):
    """Who made it, and where.

    Was a free-text string on the registration, which meant "Cipla",
    "Cipla Ltd" and "CIPLA LIMITED" were three manufacturers. A depot
    deciding what to import needs the country and the GMP status as
    facts it can filter on, not prose.
    """

    name = models.CharField(max_length=150)
    country_of_origin = models.CharField(max_length=80, blank=True)
    #: Good Manufacturing Practice. A purchasing decision, not a label —
    #: a depot may be barred from importing from an uncertified site.
    gmp_certified = models.BooleanField(default=True)
    gmp_expiry = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "catalog_manufacturer"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"], name="uq_manufacturer_name"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class DosageForm(models.TextChoices):
    """How the medicine presents. Drives packaging and dispensing."""

    TABLET = "TABLET", "Tablet"
    CAPSULE = "CAPSULE", "Capsule"
    SYRUP = "SYRUP", "Syrup"
    SUSPENSION = "SUSPENSION", "Suspension"
    INJECTION = "INJECTION", "Injection"
    INFUSION = "INFUSION", "Infusion"
    CREAM = "CREAM", "Cream"
    OINTMENT = "OINTMENT", "Ointment"
    GEL = "GEL", "Gel"
    DROPS = "DROPS", "Drops"
    INHALER = "INHALER", "Inhaler"
    SUPPOSITORY = "SUPPOSITORY", "Suppository"
    PATCH = "PATCH", "Patch"
    POWDER = "POWDER", "Powder"
    SACHET = "SACHET", "Sachet"
    DEVICE = "DEVICE", "Device"
    OTHER = "OTHER", "Other"


class Route(models.TextChoices):
    ORAL = "ORAL", "Oral"
    TOPICAL = "TOPICAL", "Topical"
    INTRAVENOUS = "INTRAVENOUS", "Intravenous"
    INTRAMUSCULAR = "INTRAMUSCULAR", "Intramuscular"
    SUBCUTANEOUS = "SUBCUTANEOUS", "Subcutaneous"
    INHALATION = "INHALATION", "Inhalation"
    OPHTHALMIC = "OPHTHALMIC", "Ophthalmic"
    OTIC = "OTIC", "Otic"
    NASAL = "NASAL", "Nasal"
    RECTAL = "RECTAL", "Rectal"
    VAGINAL = "VAGINAL", "Vaginal"
    NOT_APPLICABLE = "NOT_APPLICABLE", "Not applicable"


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

    # -- clinical identity -------------------------------------------------
    #
    # These were reachable only through ProductRegistration, which made a
    # product without a registration unsearchable by form or strength —
    # and consumables and cosmetics have no registration at all.
    dosage_form = models.CharField(
        max_length=20, choices=DosageForm.choices, blank=True
    )
    strength = models.CharField(max_length=60, blank=True, help_text='e.g. "500mg", "10mg/ml"')
    route = models.CharField(max_length=20, choices=Route.choices, blank=True)
    manufacturer = models.ForeignKey(
        Manufacturer, null=True, blank=True, on_delete=models.PROTECT, related_name="products"
    )

    # -- storage and handling ---------------------------------------------
    #
    # Cold chain alone is too blunt. "2–8°C" and "below 25°C" are both
    # constraints, and a depot has to prove it honoured the right one.
    storage_min_c = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True
    )
    storage_max_c = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True
    )
    light_sensitive = models.BooleanField(default=False)
    moisture_sensitive = models.BooleanField(default=False)

    # -- logistics ---------------------------------------------------------
    #: Of one base unit, in grams. Shipping is quoted on weight.
    unit_weight_g = models.IntegerField(null=True, blank=True)
    #: "L×W×H cm" of the outer pack, for load planning.
    pack_dimensions = models.CharField(max_length=30, blank=True)

    #: Below this, in base units, the pharmacy is told to reorder.
    reorder_point_base = models.BigIntegerField(default=0)

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


class ProductImage(TenantModel):
    """A picture of the actual pack.

    A buyer ordering from a screen cannot pick the box up. The image is
    how they confirm the presentation is the one they stock — same
    strength, same pack count, same manufacturer's artwork — before
    committing to a carton of it.

    That makes it verification, not decoration, which is why `alt` is
    required: a description a screen reader can speak is the same
    information the image carries, and a product with neither is a
    product nobody can check.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="products/%Y/%m/")
    alt = models.CharField(max_length=200, help_text="What the picture shows.")
    #: The one used in lists and cards. Exactly one per product.
    is_primary = models.BooleanField(default=False)
    position = models.IntegerField(default=0)

    class Meta:
        db_table = "catalog_product_image"
        ordering = ["position", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["product"],
                condition=models.Q(is_primary=True),
                name="uq_one_primary_image",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product.name} image"


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


# --------------------------------------------------------------------------
# Clinical reference data
# --------------------------------------------------------------------------


class ClinicalAttributeKind(models.TextChoices):
    """Facts about a product that a dispensing check compares against.

    Every one of these is a **recorded, sourced value** — never inferred,
    never computed. The system asserts an equality or an inequality
    between two stored numbers; it does not form a clinical judgement.
    That line is what keeps this the right side of "no clinical advice".
    """

    MIN_AGE_YEARS = "MIN_AGE_YEARS", "Minimum age, years"
    MAX_AGE_YEARS = "MAX_AGE_YEARS", "Maximum age, years"
    MAX_DAILY_DOSE_BASE = "MAX_DAILY_DOSE_BASE", "Maximum daily dose, base units"
    PREGNANCY_RESTRICTED = "PREGNANCY_RESTRICTED", "Restricted in pregnancy"
    #: The active ingredient, for allergy matching. Held separately from
    #: `generic_name` because one product can carry several.
    ACTIVE_INGREDIENT = "ACTIVE_INGREDIENT", "Active ingredient"


class ClinicalAttribute(TenantModel):
    """One sourced clinical fact about a product, with the dates it applied.

    Effective-dated on the same footing as a tax rule. A dispensing
    decision from eight months ago must stay explainable under the
    reference data that applied then — a maximum dose revised downwards
    last month must not make last year's dispensing look reckless.

    `source` is required. A clinical threshold with no cited origin is an
    opinion, and this system does not hold opinions about medicines.
    """

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="clinical_attributes"
    )
    kind = models.CharField(max_length=24, choices=ClinicalAttributeKind.choices)

    #: Numeric for a dose or an age, text for an ingredient, and the
    #: boolean kinds use `value_number` 1/0. One column rather than three
    #: mostly-null ones.
    value_number = models.BigIntegerField(null=True, blank=True)
    value_text = models.CharField(max_length=200, blank=True)

    source = models.CharField(
        max_length=200, help_text="Where this came from — SmPC, Rwanda FDA, monograph."
    )
    source_reference = models.CharField(max_length=200, blank=True)

    effective_from = models.DateField(default=timezone.localdate)
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "catalog_clinical_attribute"
        ordering = ["kind", "-effective_from"]
        indexes = [
            models.Index(fields=["product", "kind", "effective_from"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(source=""), name="ck_clinical_source_required"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product.name} {self.kind}"
