"""Turning records into the frozen render context.

Everything a template needs is resolved here, once, and stored on the
`Document`. After that the document no longer depends on the database:
a product renamed next year does not rewrite last year's invoice.

Money arrives already formatted. A template that has to divide by a
currency exponent is a template that will eventually get it wrong.
"""

from __future__ import annotations

from django.utils import timezone

from core.money import MINOR_UNITS, Money
from core.quantity import from_base


def money(amount: int, currency: str = "RWF") -> str:
    """Grouped, with the currency code stripped.

    `Money.__str__` already renders the right number of decimals for the
    currency — none for RWF, two for USD — and groups the thousands. The
    code is dropped because docs/18 puts it in the column header rather
    than repeating it on every row.
    """
    text = str(Money(amount, currency))
    return text.removeprefix(f"{currency} ").strip()


def quantity(base_value: int, uom) -> str:
    """A count in the unit it was ordered in, not in base units.

    "2 cartons" is what the picker reads on the shelf; "2,400 capsules"
    is the same fact in a form nobody can count.
    """
    quantity_in_uom = from_base(base_value, uom)
    plural = "s" if quantity_in_uom.value != 1 else ""
    return f"{quantity_in_uom.value:,} {uom.name.lower() if uom.name else uom.code.lower()}{plural}"


def when(value) -> str:
    """Stored UTC, printed Kigali. The reader is standing in Rwanda."""
    if value is None:
        return ""
    return timezone.localtime(value).strftime("%d %b %Y %H:%M")


def day(value) -> str:
    return value.strftime("%d %b %Y") if value else ""


def party(organization, *, licence_kind: str | None = None) -> dict:
    """The From or To block.

    The premises licence is looked up rather than stored on the
    organization: capability comes from held licences, and the number a
    document must show is the one for the activity it documents.
    """
    licence = ""
    if licence_kind:
        held = organization.licences.filter(kind=licence_kind).order_by("-expiry").first()
        licence = held.number if held else ""
    return {
        "name": organization.name,
        "tin": organization.tin,
        "licence": licence,
        "address": "",
    }


def base(*, doc_type: str, number: str, issuer, recipient, status: str = "") -> dict:
    """The regions every document shares."""
    return {
        "doc_type": doc_type,
        "number": number,
        "version": 1,
        "supersedes": "",
        "status": status,
        "issued_on": day(timezone.localdate()),
        "issuer": issuer,
        "recipient": recipient,
        "verification_reference": number,
    }


# --------------------------------------------------------------------------
# Per-document builders
# --------------------------------------------------------------------------


def picking_ticket(shipment) -> dict:
    """Internal. Ordered the way the picker walks the warehouse.

    FEFO order — earliest expiry first — because the picker reaching for
    the nearest carton is exactly the failure the rule exists to prevent.
    The ticket therefore names the batch, not just the product.
    """
    order = shipment.order
    lines = sorted(
        shipment.lines.select_related("product", "uom", "batch"),
        key=lambda line: (line.expiry_date, line.product.name),
    )
    context = base(
        doc_type="Picking ticket",
        number="",
        issuer=party(shipment.organization),
        recipient=party(order.organization),
    )
    context.update(
        {
            "order_number": order.number,
            "from_location": shipment.from_location.name,
            "lines": [
                {
                    "product": line.product.name,
                    "strength": line.product.strength,
                    "batch": line.batch_number,
                    "expiry": day(line.expiry_date),
                    "quantity": quantity(line.quantity_base, line.uom),
                    "cold_chain": line.product.cold_chain,
                }
                for line in lines
            ],
        }
    )
    return context


def delivery_note(shipment) -> dict:
    """What the receiving pharmacist checks the cartons against."""
    order = shipment.order
    lines = shipment.lines.select_related("product", "uom").order_by("product__name")
    context = base(
        doc_type="Delivery note",
        number=shipment.number,
        issuer=party(shipment.organization, licence_kind="WHOLESALE"),
        recipient=party(order.organization, licence_kind="RETAIL"),
        status="Dispatched",
    )
    context.update(
        {
            "order_number": order.number,
            "dispatched_at": when(shipment.dispatched_at),
            "carrier": shipment.carrier,
            "vehicle": shipment.vehicle_registration,
            "driver": shipment.driver_name,
            "driver_licence": shipment.driver_licence,
            "deliver_to": order.deliver_to.name,
            "lines": [
                {
                    "product": line.product.name,
                    "strength": line.product.strength,
                    "batch": line.batch_number,
                    "expiry": day(line.expiry_date),
                    "quantity": quantity(line.quantity_base, line.uom),
                    "cold_chain": line.product.cold_chain,
                }
                for line in lines
            ],
        }
    )
    return context


def invoice(invoice_record) -> dict:
    """Proforma or tax invoice — the same body, a different demand.

    Tax is read off the line, never recomputed. The rate that applied on
    the day it was issued is what the document must still show in five
    years, whatever the rule table says by then.
    """
    is_proforma = invoice_record.kind == "PROFORMA"
    is_credit_note = invoice_record.kind == "CREDIT_NOTE"
    lines = invoice_record.lines.select_related("product", "uom")
    context = base(
        doc_type=(
            "Credit note"
            if is_credit_note
            else "Proforma invoice" if is_proforma else "Commercial tax invoice"
        ),
        number=invoice_record.number,
        issuer=party(invoice_record.organization, licence_kind="WHOLESALE"),
        recipient=party(invoice_record.customer, licence_kind="RETAIL"),
        status=invoice_record.get_status_display(),
    )
    context.update(
        {
            "currency": invoice_record.currency,
            "order_number": invoice_record.order.number if invoice_record.order_id else "",
            "issued_on_date": day(invoice_record.issued_on),
            "due_on": day(invoice_record.due_on),
            "terms": (
                "Payable on receipt"
                if invoice_record.payment_terms_days == 0
                else f"Net {invoice_record.payment_terms_days} days"
            ),
            "is_proforma": is_proforma,
            "is_credit_note": is_credit_note,
            "against": invoice_record.against.number if invoice_record.against_id else "",
            "reason": invoice_record.reason,
            "lines": [
                {
                    "description": line.description,
                    "quantity": f"{line.quantity:,} {line.uom.code.lower()}",
                    "unit_price": money(line.unit_price, invoice_record.currency),
                    "tax_treatment": line.tax_treatment.title(),
                    "tax_rate": f"{line.tax_rate_basis_points / 100:g}%",
                    "tax_amount": money(line.tax_amount, invoice_record.currency),
                    "line_total": money(
                        line.line_subtotal + line.tax_amount, invoice_record.currency
                    ),
                }
                for line in lines
            ],
            "subtotal": money(invoice_record.subtotal, invoice_record.currency),
            "tax_total": money(invoice_record.tax_total, invoice_record.currency),
            "total": money(invoice_record.total, invoice_record.currency),
            "settled": money(invoice_record.settled, invoice_record.currency),
            "outstanding": money(invoice_record.outstanding, invoice_record.currency),
        }
    )
    return context


def goods_receipt_note(receipt) -> dict:
    """Ordered, received, accepted, rejected as four columns.

    The discrepancy is the point of the document, so the four numbers sit
    side by side rather than being reconciled into one.
    """
    lines = receipt.lines.select_related("product", "uom")
    context = base(
        doc_type="Goods receipt note",
        number=receipt.number,
        issuer=party(receipt.organization),
        recipient=party(receipt.supplier) if receipt.supplier_id else {"name": "—"},
        status=receipt.get_status_display(),
    )
    context.update(
        {
            "order_number": receipt.order.number if receipt.order_id else "",
            "location": receipt.location.name,
            "posted_at": when(receipt.posted_at),
            "lines": [
                {
                    "product": line.product.name,
                    "uom": line.uom.code.lower(),
                    "batch": line.batch_number,
                    "expiry": day(line.expiry_date),
                    "ordered": line.ordered,
                    "received": line.received,
                    "accepted": line.accepted,
                    "rejected": line.rejected,
                    "reason": line.rejection_reason,
                }
                for line in lines
            ],
        }
    )
    return context


def controlled_transfer(transfer) -> dict:
    """The regulated one. Two named pharmacists, both signing.

    Every scheduled line is listed with its batch, because the register
    the regulator inspects is kept per batch, not per product.
    """
    shipment = transfer.shipment
    order = shipment.order
    lines = [
        line
        for line in shipment.lines.select_related("product", "uom")
        if line.product.legal_status == "CONTROLLED"
    ]
    context = base(
        doc_type="Controlled substance transfer",
        number=transfer.number,
        issuer=party(transfer.organization, licence_kind="WHOLESALE"),
        recipient=party(order.organization, licence_kind="RETAIL"),
        status="Released" if transfer.is_released else "Draft",
    )
    context.update(
        {
            "delivery_note": shipment.number,
            "order_number": order.number,
            "released_by": str(transfer.released_by.user),
            "released_registration": transfer.released_by.council_number,
            "released_at": when(transfer.released_at),
            "received_by": transfer.received_by_name,
            "received_registration": transfer.received_by_registration,
            "received_at": when(transfer.received_at),
            "lines": [
                {
                    "product": line.product.name,
                    "schedule": line.product.controlled_schedule,
                    "batch": line.batch_number,
                    "expiry": day(line.expiry_date),
                    "quantity": quantity(line.quantity_base, line.uom),
                }
                for line in lines
            ],
        }
    )
    return context
