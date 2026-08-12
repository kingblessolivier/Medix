"""Catalog rules: attribute validation and UoM chain integrity."""

from __future__ import annotations

from typing import Any

from core.exceptions import DomainError
from catalog.models import AttributeDefinition, Product, ProductType, UnitOfMeasure


class AttributeError_(DomainError):
    default_code = "invalid_attributes"
    default_detail = "Product attributes do not match this product type."


def validate_attributes(product_type: ProductType, attributes: dict[str, Any]) -> dict[str, Any]:
    """Check a product's JSON attributes against its type's definitions.

    Preserves: attributes stay honest despite being schemaless on disk.
    """
    definitions = {d.code: d for d in AttributeDefinition.objects.filter(product_type=product_type)}

    unknown = set(attributes) - set(definitions)
    if unknown:
        raise AttributeError_(
            f"Unknown attributes for {product_type.code}: {', '.join(sorted(unknown))}.",
            meta={"unknown": sorted(unknown)},
        )

    missing = [c for c, d in definitions.items() if d.required and not attributes.get(c)]
    if missing:
        raise AttributeError_(
            f"Missing required attributes: {', '.join(sorted(missing))}.",
            meta={"missing": sorted(missing)},
        )

    for code, value in attributes.items():
        definition = definitions[code]
        if definition.data_type == "NUMBER" and not isinstance(value, (int, float)):
            raise AttributeError_(f"{definition.label} must be a number.", meta={"field": code})
        if definition.data_type == "BOOLEAN" and not isinstance(value, bool):
            raise AttributeError_(f"{definition.label} must be true or false.", meta={"field": code})
        if definition.data_type == "ENUM" and value not in (definition.enum_values or []):
            raise AttributeError_(
                f"{definition.label} must be one of: {', '.join(definition.enum_values or [])}.",
                meta={"field": code},
            )

    return attributes


def validate_uom_chain(product: Product) -> None:
    """A chain needs exactly one base unit at factor 1, and no duplicates."""
    units = list(UnitOfMeasure.objects.filter(product=product))
    if not units:
        raise DomainError("Product has no unit of measure.", code="uom_missing")

    bases = [u for u in units if u.is_base]
    if len(bases) != 1:
        raise DomainError("A product needs exactly one base unit.", code="uom_base")
    if bases[0].factor_to_base != 1:
        raise DomainError("The base unit must have factor 1.", code="uom_base_factor")

    factors = [u.factor_to_base for u in units]
    if len(set(factors)) != len(factors):
        raise DomainError("Two units share a conversion factor.", code="uom_duplicate_factor")


def resolve_scan(organization, scanned: str) -> dict:
    """Turn a scanned pack into the product and batch it identifies.

    Preserves: a scan never invents data. Whatever the barcode does not
    carry comes back as null, and an unmatched GTIN is reported as
    unmatched rather than guessed at.

    See docs/06-compliance.md §10.
    """
    from catalog.gs1 import parse as parse_gs1
    from catalog.models import Product
    from inventory.models import Batch

    pack = parse_gs1(scanned)

    product = None
    if pack.gtin:
        product = Product.objects.filter(organization=organization, gtin=pack.gtin).first()

    batch = None
    if pack.batch_number:
        candidates = Batch.objects.filter(
            organization=organization, batch_number=pack.batch_number
        )
        if product is not None:
            candidates = candidates.filter(product=product)
        batch = candidates.select_related("product").first()
        # A batch found by number identifies its product even when the
        # GTIN is absent or unregistered.
        if batch is not None and product is None:
            product = batch.product

    return {
        "gtin": pack.gtin,
        "batch_number": pack.batch_number,
        "expiry_date": pack.expiry_date,
        "serial": pack.serial,
        "product": product,
        "batch": batch,
        "matched": product is not None,
    }


def mirror_product(*, organization, source: Product, performed_by=None) -> Product:
    """The receiving pharmacy's own row for a product it bought.

    Catalogs are tenant-scoped: a pharmacy sets its own pricing, tax
    treatment and packaging chain, so a purchase order line points at the
    *supplier's* product while the buyer's ledger must key to the buyer's
    own. Receiving is where the two meet.

    Identity across organizations is the Rwanda FDA registration number,
    then the GTIN — both national identifiers. Name is deliberately not a
    fallback: two pharmacies spelling a product differently is normal, and
    matching on it would silently merge distinct products.

    The unit chain is copied factor for factor. It has to be: every ledger
    quantity is stored in base units, so a pack that means 24 at the
    supplier and 12 here would corrupt the received quantity rather than
    fail loudly.
    """
    if source.organization_id == organization.id:
        return source

    registration = getattr(source, "registration", None)
    if registration is not None and registration.registration_number:
        existing = Product.objects.filter(
            organization=organization,
            registration__registration_number=registration.registration_number,
        ).first()
        if existing is not None:
            return existing

    if source.gtin:
        existing = Product.objects.filter(organization=organization, gtin=source.gtin).first()
        if existing is not None:
            return existing

    product_type, _ = ProductType.objects.get_or_create(
        organization=organization,
        code=source.product_type.code,
        defaults={"name": source.product_type.name},
    )

    mirrored = Product.objects.create(
        organization=organization,
        product_type=product_type,
        name=source.name,
        generic_name=source.generic_name,
        brand=source.brand,
        attributes=source.attributes,
        legal_status=source.legal_status,
        controlled_schedule=source.controlled_schedule,
        tax_treatment=source.tax_treatment,
        cold_chain=source.cold_chain,
        gtin=source.gtin,
        created_by=performed_by,
    )
    UnitOfMeasure.objects.bulk_create(
        [
            UnitOfMeasure(
                organization=organization,
                product=mirrored,
                code=unit.code,
                name=unit.name,
                factor_to_base=unit.factor_to_base,
                is_base=unit.is_base,
                is_purchase_default=unit.is_purchase_default,
                is_dispense_default=unit.is_dispense_default,
                is_sellable=unit.is_sellable,
            )
            for unit in source.units.all()
        ]
    )

    # The registration travels with the product: without it the buyer
    # cannot tell whether what they are holding is still dispensable.
    if registration is not None:
        from catalog.models import ProductRegistration

        ProductRegistration.objects.create(
            organization=organization,
            product=mirrored,
            registration_number=registration.registration_number,
            holder=registration.holder,
            local_agent=registration.local_agent,
            strength=registration.strength,
            dosage_form=registration.dosage_form,
            route=registration.route,
            pack_size=registration.pack_size,
            shelf_life_months=registration.shelf_life_months,
            manufacturer=registration.manufacturer,
            manufacturer_country=registration.manufacturer_country,
            registered_on=registration.registered_on,
            registration_expiry=registration.registration_expiry,
            status=registration.status,
            created_by=performed_by,
        )
    return mirrored
