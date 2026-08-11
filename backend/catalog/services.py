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
