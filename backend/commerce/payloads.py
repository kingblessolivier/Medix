"""The transfer payload — an ASN the buyer does not have to re-key.

The depot already knows the batch, the expiry, the packaging chain and
what it charged. Making the receiving pharmacist type all of it again is
where wrong expiry dates and invented batch numbers come from.

So dispatch emits this, and receiving consumes it: the buyer's goods
receipt arrives pre-filled and they confirm or correct it. Correcting
stays possible on purpose — a receipt that cannot disagree with the
delivery note would hide every short delivery.

**Three deviations from the schema this was specified against**, each
argued in docs/28:

* **Base units only.** Separate pack and loose counters drift and then
  need reconciling. One number; `core.quantity.split_to_units` derives
  the display split.
* **No four-decimal unit cost.** `core.pricing.derive` returns the
  rounding alongside the price so a screen can show it rather than
  absorb it.
* **Tax treatment, not a rate.** A rate frozen into a payload is wrong
  the moment the rule changes. The treatment resolves against the
  receiver's own effective-dated `TaxRule`.
"""

from __future__ import annotations

import hashlib
import json

from django.db import transaction
from django.utils import timezone

from catalog import services as catalog
from commerce.models import (
    GoodsReceipt,
    GoodsReceiptStatus,
    PurchaseOrder,
    Shipment,
)
from core.exceptions import DomainError
from core.models import Organization, User

#: Bumped when the shape changes in a way an older reader cannot handle.
#: A receiver that does not recognise the version refuses rather than
#: guessing which fields it is missing.
SCHEMA = "medix.transfer/1"


class UnsupportedSchema(DomainError):
    default_code = "unsupported_schema"
    default_detail = "This transfer file was written by an incompatible version."


class WrongRecipient(DomainError):
    default_code = "wrong_recipient"
    default_detail = "This transfer was addressed to another pharmacy."


def payload_hash(payload: dict) -> str:
    """Stable across key order, so re-serialising does not look like a change."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


# --------------------------------------------------------------------------
# Emitting
# --------------------------------------------------------------------------


def _units(product) -> list[dict]:
    return [
        {
            "code": unit.code,
            "name": unit.name,
            "factor_to_base": unit.factor_to_base,
            "is_base": unit.is_base,
            "is_purchase_default": unit.is_purchase_default,
            "is_dispense_default": unit.is_dispense_default,
            "is_sellable": unit.is_sellable,
        }
        for unit in product.units.all().order_by("-factor_to_base")
    ]


def _product_descriptor(product) -> dict:
    """Everything the receiver needs to create this product properly.

    Registration and GTIN are the identity; the rest is what a pharmacy
    must hold to store, price and dispense the thing legally.
    """
    registration = getattr(product, "registration", None)
    manufacturer = product.manufacturer
    return {
        "registration_number": registration.registration_number if registration else "",
        "gtin": product.gtin,
        "name": product.name,
        "generic_name": product.generic_name,
        "brand": product.brand,
        "product_type": product.product_type.code,
        "product_type_name": product.product_type.name,
        "dosage_form": product.dosage_form,
        "strength": product.strength,
        "route": product.route,
        "legal_status": product.legal_status,
        "controlled_schedule": product.controlled_schedule,
        "tax_treatment": product.tax_treatment,
        "cold_chain": product.cold_chain,
        "storage_min_c": str(product.storage_min_c) if product.storage_min_c is not None else None,
        "storage_max_c": str(product.storage_max_c) if product.storage_max_c is not None else None,
        "light_sensitive": product.light_sensitive,
        "moisture_sensitive": product.moisture_sensitive,
        "manufacturer": (
            {
                "name": manufacturer.name,
                "country": manufacturer.country_of_origin,
                "gmp_certified": manufacturer.gmp_certified,
            }
            if manufacturer is not None
            else None
        ),
        "units": _units(product),
    }


def build_transfer_payload(*, shipment: Shipment) -> dict:
    """What left the depot, in a form the buyer's system can apply.

    Keyed on the delivery note number: the physical paperwork and the
    electronic notice name the same event, so a receiving clerk holding
    one can find the other.
    """
    order = shipment.order
    lines = shipment.lines.select_related(
        "product__product_type", "product__manufacturer", "product__registration", "uom"
    )

    return {
        "schema": SCHEMA,
        "transfer_id": shipment.number,
        "order_number": order.number,
        "source_organization": {
            "id": str(shipment.organization_id),
            "name": shipment.organization.name,
            "tin": shipment.organization.tin,
        },
        "destination_organization": {
            "id": str(order.organization_id),
            "name": order.organization.name,
        },
        "dispatched_at": (
            shipment.dispatched_at.isoformat() if shipment.dispatched_at else None
        ),
        "carrier": shipment.carrier,
        "lines": [
            {
                "product": _product_descriptor(line.product),
                "batch_number": line.batch_number,
                "manufacture_date": (
                    line.batch.manufacture_date.isoformat()
                    if line.batch.manufacture_date
                    else None
                ),
                "expiry_date": line.expiry_date.isoformat(),
                # Base units, always. The receiver splits for display.
                "quantity_base": line.quantity_base,
                "uom_code": line.uom.code,
                # What the depot paid, so the buyer's cost basis starts
                # from a real number rather than from the sell price.
                "unit_cost_base": line.batch.unit_cost_base,
                # Per the depot's listing unit, which the receiver
                # resolves against its own chain. A starting point for
                # the buyer's retail price, never a price they must use.
                "srp": _srp_for(shipment.organization_id, line.product_id),
                "currency": order.currency,
                "cold_chain": line.product.cold_chain,
            }
            for line in lines
        ],
    }


def _srp_for(organization_id, product_id) -> dict | None:
    """The depot's suggested retail price, if it published one."""
    from commerce.models import VendorListing

    listing = VendorListing.objects.filter(
        organization_id=organization_id, product_id=product_id, srp__isnull=False
    ).select_related("price_uom").first()
    if listing is None:
        return None
    return {"amount": listing.srp, "uom_code": listing.price_uom.code}


# --------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------


@transaction.atomic
def apply_transfer_payload(
    *,
    payload: dict,
    organization: Organization,
    location,
    performed_by: User,
    order: PurchaseOrder | None = None,
) -> GoodsReceipt:
    """Turn a transfer into a draft goods receipt the buyer confirms.

    **Draft, not posted.** Nothing enters the ledger until a human has
    counted the cartons. The payload says what was sent; only the
    receiving pharmacist can say what arrived, and collapsing those two
    is how a short delivery becomes invisible.

    Idempotent on the payload hash. A retried transmission returns the
    receipt already created rather than a second one, because two draft
    receipts for one delivery is how stock gets received twice.
    """
    if payload.get("schema") != SCHEMA:
        raise UnsupportedSchema(
            f"Expected {SCHEMA}, got {payload.get('schema', 'nothing')}.",
            meta={"schema": payload.get("schema")},
        )

    destination = (payload.get("destination_organization") or {}).get("id")
    if destination and str(destination) != str(organization.id):
        raise WrongRecipient(
            f"Addressed to {(payload.get('destination_organization') or {}).get('name', 'another pharmacy')}.",
        )

    digest = payload_hash(payload)
    existing = GoodsReceipt.objects.filter(
        organization=organization, transfer_hash=digest
    ).first()
    if existing is not None:
        return existing

    from commerce import services as commerce

    receipt = commerce.start_receipt(
        organization=organization,
        location=location,
        performed_by=performed_by,
        order=order,
        supplier=_resolve_supplier(payload),
    )
    receipt.transfer_id = payload.get("transfer_id", "")
    receipt.transfer_hash = digest
    receipt.save(update_fields=["transfer_id", "transfer_hash"])

    # Keyed on the national identifiers, not on a product id. The order
    # lines point at the *supplier's* product row and the receipt lines
    # will point at the buyer's, so the ids never match — the
    # registration number and GTIN are the only thing both sides share.
    order_lines: dict[str, list] = {}
    if order is not None:
        for line in order.lines.select_related("product__registration"):
            for key in _identity_keys(line.product):
                order_lines.setdefault(key, []).append(line)

    for entry in payload.get("lines", []):
        product = catalog.ensure_product(
            organization=organization,
            descriptor=entry["product"],
            performed_by=performed_by,
        )
        uom = _receiving_uom(product, entry)
        if entry["quantity_base"] % uom.factor_to_base:
            # The depot shipped a quantity that does not divide into the
            # buyer's unit. Fall back to the base unit rather than
            # rounding: a rounded receipt is a wrong receipt.
            uom = product.base_uom

        commerce.add_receipt_line(
            receipt=receipt,
            product=product,
            uom=uom,
            received=entry["quantity_base"] // uom.factor_to_base,
            batch_number=entry["batch_number"],
            expiry_date=_as_date(entry["expiry_date"]),
            unit_cost_base=entry.get("unit_cost_base", 0),
            order_line=_match_order_line(order_lines, entry["product"]),
        )

    return receipt


def _resolve_supplier(payload: dict) -> Organization | None:
    """The depot, when it is on this instance. None when it is not.

    An off-instance supplier is a real case — the payload may have
    arrived by file — and a receipt from a supplier we have no row for is
    still a receipt.
    """
    source = (payload.get("source_organization") or {}).get("id")
    if not source:
        return None
    return Organization.objects.filter(id=source).first()


def _receiving_uom(product, entry: dict):
    """The unit the buyer will count in.

    Prefer the one the depot shipped in — the cartons on the pallet are
    labelled that way — then the buyer's own purchase default.
    """
    shipped = product.units.filter(code=entry.get("uom_code", "")).first()
    if shipped is not None:
        return shipped
    default = product.units.filter(is_purchase_default=True).first()
    return default or product.base_uom


def _identity_keys(product) -> list[str]:
    """How this product is named across organizations.

    Registration number and GTIN, each namespaced so a registration
    number that happens to equal somebody's barcode cannot collide.
    """
    registration = getattr(product, "registration", None)
    keys = []
    if registration is not None and registration.registration_number:
        keys.append(f"reg:{registration.registration_number}")
    if product.gtin:
        keys.append(f"gtin:{product.gtin}")
    return keys


def _match_order_line(order_lines: dict, descriptor: dict):
    """Link the receipt line back to what was ordered, when there is one.

    Matched on identity alone, never on unit: the depot may ship in a
    different unit from the one ordered — a carton against five packs —
    and refusing to link on that basis would leave the order looking
    permanently outstanding.
    """
    for key in (
        f"reg:{descriptor.get('registration_number') or ''}",
        f"gtin:{descriptor.get('gtin') or ''}",
    ):
        candidates = order_lines.get(key)
        if candidates:
            return candidates[0]
    return None


def _as_date(value):
    from datetime import date

    if isinstance(value, date):
        return value
    return date.fromisoformat(value)
