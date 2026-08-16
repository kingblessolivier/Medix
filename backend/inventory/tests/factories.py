"""Shared fixtures. Factories, never fixtures files — those drift."""

from __future__ import annotations

from datetime import date, timedelta

from catalog.models import (
    LegalStatus,
    Product,
    ProductType,
    ProductTypeCode,
    TaxTreatment,
    UnitOfMeasure,
)
from core.models import Organization
from inventory.models import Batch, Location, LocationKind, TemperatureClass


def make_org(name: str = "Kigali Care Pharmacy", kind: str = "RETAIL") -> Organization:
    return Organization.objects.create(name=name, primary_kind=kind)


def make_location(
    org: Organization,
    name: str = "Main Store",
    code: str = "MAIN",
    temperature_class: str = TemperatureClass.AMBIENT,
) -> Location:
    return Location.objects.create(
        organization=org,
        kind=LocationKind.STORE,
        name=name,
        code=code,
        temperature_class=temperature_class,
    )


def make_product(
    org: Organization,
    name: str = "Amoxicillin 500mg",
    *,
    legal_status: str = LegalStatus.POM,
    cold_chain: bool = False,
    tax: str = TaxTreatment.EXEMPT,
) -> Product:
    """Product with a full UoM chain: carton → pack → blister → capsule."""
    product_type, _ = ProductType.objects.get_or_create(
        organization=org,
        code=ProductTypeCode.MEDICINE,
        defaults={"name": "Medicine"},
    )
    product = Product.objects.create(
        organization=org,
        product_type=product_type,
        name=name,
        generic_name=name.split()[0].lower(),
        legal_status=legal_status,
        controlled_schedule="I" if legal_status == LegalStatus.CONTROLLED else "",
        tax_treatment=tax,
        cold_chain=cold_chain,
    )
    for code, label, factor, base in [
        ("CARTON", "Carton of 12 packs", 1200, False),
        ("PACK", "Pack of 100 capsules", 100, False),
        ("BLISTER", "Blister of 10", 10, False),
        ("UNIT", "Capsule", 1, True),
    ]:
        UnitOfMeasure.objects.create(
            organization=org,
            product=product,
            code=code,
            name=label,
            factor_to_base=factor,
            is_base=base,
            is_purchase_default=(code == "PACK"),
            is_dispense_default=base,
        )
    return product


def make_batch(
    org: Organization,
    product: Product,
    *,
    number: str = "AMX-0021",
    expires_in_days: int = 365,
    unit_cost_base: int = 280,
    cold_chain: bool = False,
) -> Batch:
    return Batch.objects.create(
        organization=org,
        product=product,
        batch_number=number,
        expiry_date=date.today() + timedelta(days=expires_in_days),
        unit_cost_base=unit_cost_base,
        cold_chain=cold_chain,
    )


def uom(product: Product, code: str) -> UnitOfMeasure:
    return UnitOfMeasure.objects.get(product=product, code=code)
