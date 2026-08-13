"""Issued documents.

The determinism test is the load-bearing one: the same stored context
must render the same bytes, or "reprint" means "render something new and
hope it matches".
"""

from datetime import date, timedelta

import pytest

from catalog.models import LegalStatus
from commerce import invoicing, services
from commerce.models import ControlledTransfer, InvoiceKind, TradingRelationship
from core.models import (
    AuditEvent,
    Branch,
    LicenceKind,
    LicenceStatus,
    PharmacistRegistration,
    PremisesLicence,
    User,
)
from core.quantity import Quantity
from documents import context as build
from documents import services as documents
from documents.models import Document, DocumentKind
from documents.render import content_hash, render_html
from documents.tokens import PRINT_TOKENS, print_palette
from inventory import services as inventory
from inventory.models import MovementKind
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db


def licence(org, kind, *, number=None):
    branch, _ = Branch.objects.get_or_create(
        organization=org, code="MAIN", defaults={"name": "Main"}
    )
    return PremisesLicence.objects.create(
        organization=org,
        branch=branch,
        kind=kind,
        number=number or f"RFDA-{kind}-{org.name[:4]}",
        issued_on=date.today() - timedelta(days=400),
        expiry=date.today() + timedelta(days=365),
        status=LicenceStatus.ACTIVE,
    )


@pytest.fixture
def shipped():
    """An order taken all the way to dispatch, so documents exist."""
    wholesale = make_org("ABC Wholesale", kind=LicenceKind.WHOLESALE_PHARMACY)
    retail = make_org("Kigali Care", kind=LicenceKind.RETAIL_PHARMACY)
    licence(wholesale, LicenceKind.WHOLESALE_PHARMACY)
    licence(retail, LicenceKind.RETAIL_PHARMACY)
    wholesale.tin = "102938475"
    wholesale.save(update_fields=["tin"])

    seller = User.objects.create_user(username="jean", password="x", organization=wholesale)
    buyer = User.objects.create_user(username="marie", password="x", organization=retail)
    owner = User.objects.create_user(username="claudine", password="x", organization=retail)

    product = make_product(wholesale, "Amoxicillin 500mg", legal_status=LegalStatus.POM)
    product.strength = "500mg"
    product.save(update_fields=["strength"])

    depot = make_location(wholesale, "ABC Depot", "DEP")
    store = make_location(retail, "Main Store", "MAIN")

    for number, days in [("AMX-EARLY", 120), ("AMX-LATE", 400)]:
        batch = make_batch(wholesale, product, number=number, expires_in_days=days)
        inventory.post_movement(
            organization=wholesale,
            location=depot,
            batch=batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(10, uom(product, "PACK")),
        )

    TradingRelationship.objects.create(
        organization=wholesale, customer=retail, is_verified=True, credit_limit=100_000_000
    )
    listing = services.publish_listing(
        organization=wholesale,
        product=product,
        price=5_000,
        price_uom=uom(product, "PACK"),
        offered_base=Quantity(20, uom(product, "PACK")).base_value,
        performed_by=seller,
    )
    order = services.start_order(
        organization=retail, supplier=wholesale, deliver_to=store, performed_by=buyer
    )
    # Enough to span both batches, so FEFO produces two shipment lines.
    services.add_order_line(order=order, listing=listing, quantity=15)
    services.request_approval(order=order, performed_by=buyer)
    services.submit_order(order=order, performed_by=owner)
    services.confirm_order(order=order, performed_by=seller)
    shipment = services.dispatch_order(
        order=order,
        from_location=depot,
        performed_by=seller,
        carrier="Volcano Express",
        vehicle_registration="RAD 123 C",
        driver_name="Emmanuel H.",
    )
    return {
        "wholesale": wholesale, "retail": retail, "seller": seller, "buyer": buyer,
        "owner": owner, "order": order, "shipment": shipment, "product": product,
        "store": store, "depot": depot,
    }


class TestPrintTokens:
    """Colour comes from the application's tokens, never from a template."""

    def test_every_print_token_resolves(self):
        palette = print_palette()
        assert set(palette) == set(PRINT_TOKENS)

    def test_the_light_theme_wins(self):
        """Paper has no dark mode; reading a later block would invert it."""
        assert print_palette()["surface"] == "#ffffff"

    def test_ink_matches_the_documented_value(self):
        # docs/18 §Colour in print states these. If the token moves, the
        # doc is what has to be updated — not this test, quietly.
        palette = print_palette()
        assert palette["text"] == "#17212b"
        assert palette["muted"] == "#5f6b76"
        assert palette["hairline"] == "#dce2e8"

    def test_no_template_declares_a_colour(self):
        """One stylesheet, in the base. A hex in a child is a drift."""
        import re
        from pathlib import Path

        import documents

        root = Path(documents.__file__).parent / "templates" / "docs"
        offenders = []
        for template in root.glob("*.html"):
            if template.name == "base_document.html":
                continue
            if re.search(r"#[0-9a-fA-F]{3,8}\b", template.read_text(encoding="utf-8")):
                offenders.append(template.name)
        assert offenders == []


class TestDispatchIssuesPaperwork:
    def test_a_picking_ticket_is_issued(self, shipped):
        document = documents.latest(
            subject=shipped["shipment"], kind=DocumentKind.PICKING_TICKET
        )
        assert document is not None
        assert document.number.startswith("PT-")

    def test_the_delivery_note_reuses_the_shipment_number(self, shipped):
        """Issuing the paper must not burn a second number."""
        document = documents.latest(
            subject=shipped["shipment"], kind=DocumentKind.DELIVERY_NOTE
        )
        assert document.number == shipped["shipment"].number

    def test_the_picking_ticket_is_in_expiry_order(self, shipped):
        document = documents.latest(
            subject=shipped["shipment"], kind=DocumentKind.PICKING_TICKET
        )
        batches = [line["batch"] for line in document.context["lines"]]
        assert batches == ["AMX-EARLY", "AMX-LATE"]

    def test_the_picking_ticket_carries_no_price(self, shipped):
        document = documents.latest(
            subject=shipped["shipment"], kind=DocumentKind.PICKING_TICKET
        )
        assert "5,000" not in document.html

    def test_the_delivery_note_names_the_driver(self, shipped):
        document = documents.latest(
            subject=shipped["shipment"], kind=DocumentKind.DELIVERY_NOTE
        )
        assert "Emmanuel H." in document.html
        assert "RAD 123 C" in document.html

    def test_the_delivery_note_shows_batch_and_expiry(self, shipped):
        document = documents.latest(
            subject=shipped["shipment"], kind=DocumentKind.DELIVERY_NOTE
        )
        assert "AMX-EARLY" in document.html

    def test_issuing_is_audited(self, shipped):
        assert AuditEvent.objects.filter(action="documents.document.issued").count() >= 2


class TestFrozenContext:
    def test_a_rename_does_not_rewrite_the_document(self, shipped):
        """The reason the context is stored rather than re-derived."""
        document = documents.latest(
            subject=shipped["shipment"], kind=DocumentKind.DELIVERY_NOTE
        )
        original = document.html

        product = shipped["product"]
        product.name = "Amoxil 500mg"
        product.save(update_fields=["name"])

        document.refresh_from_db()
        assert document.html == original
        assert "Amoxicillin 500mg" in document.html

    def test_rendering_is_deterministic(self, shipped):
        """Same context, same bytes. Otherwise a reprint is a new document."""
        document = documents.latest(
            subject=shipped["shipment"], kind=DocumentKind.DELIVERY_NOTE
        )
        again = render_html(template=document.template, context=document.context)
        assert content_hash(again) == document.sha256


class TestReissue:
    def test_a_correction_is_a_new_version_at_the_same_number(self, shipped):
        original = documents.latest(
            subject=shipped["shipment"], kind=DocumentKind.DELIVERY_NOTE
        )
        corrected = documents.issue(
            kind=DocumentKind.DELIVERY_NOTE,
            subject=shipped["shipment"],
            organization=shipped["wholesale"],
            context=build.delivery_note(shipped["shipment"]),
            performed_by=shipped["seller"],
            supersedes=original,
        )
        assert corrected.number == original.number
        assert corrected.version == original.version + 1
        assert corrected.supersedes_id == original.id

    def test_the_original_survives_the_correction(self, shipped):
        original = documents.latest(
            subject=shipped["shipment"], kind=DocumentKind.DELIVERY_NOTE
        )
        documents.issue(
            kind=DocumentKind.DELIVERY_NOTE,
            subject=shipped["shipment"],
            organization=shipped["wholesale"],
            context=build.delivery_note(shipped["shipment"]),
            performed_by=shipped["seller"],
            supersedes=original,
        )
        original.refresh_from_db()
        assert Document.objects.filter(id=original.id).exists()
        assert original.is_amended

    def test_the_correction_says_what_it_supersedes(self, shipped):
        original = documents.latest(
            subject=shipped["shipment"], kind=DocumentKind.DELIVERY_NOTE
        )
        corrected = documents.issue(
            kind=DocumentKind.DELIVERY_NOTE,
            subject=shipped["shipment"],
            organization=shipped["wholesale"],
            context=build.delivery_note(shipped["shipment"]),
            performed_by=shipped["seller"],
            supersedes=original,
        )
        assert f"{original.number} v1" in corrected.html

    def test_kinds_cannot_be_crossed(self, shipped):
        from core.exceptions import DomainError

        original = documents.latest(
            subject=shipped["shipment"], kind=DocumentKind.DELIVERY_NOTE
        )
        with pytest.raises(DomainError):
            documents.issue(
                kind=DocumentKind.PICKING_TICKET,
                subject=shipped["shipment"],
                organization=shipped["wholesale"],
                context=build.picking_ticket(shipped["shipment"]),
                supersedes=original,
            )


class TestGoodsReceiptNote:
    def test_posting_a_receipt_issues_the_note(self, shipped):
        receipt = services.start_receipt(
            organization=shipped["retail"],
            location=shipped["store"],
            performed_by=shipped["buyer"],
            order=shipped["order"],
        )
        line = shipped["order"].lines.first()
        services.add_receipt_line(
            receipt=receipt,
            product=shipped["product"],
            uom=uom(shipped["product"], "PACK"),
            received=15,
            accepted=14,
            rejected=1,
            rejection_reason="Crushed carton",
            batch_number="AMX-EARLY",
            expiry_date=date.today() + timedelta(days=120),
            order_line=line,
        )
        services.post_receipt(receipt=receipt, performed_by=shipped["buyer"])

        document = documents.latest(subject=receipt, kind=DocumentKind.GOODS_RECEIPT)
        assert document is not None
        assert document.number == receipt.number

    def test_the_four_columns_are_all_present(self, shipped):
        receipt = services.start_receipt(
            organization=shipped["retail"],
            location=shipped["store"],
            performed_by=shipped["buyer"],
            order=shipped["order"],
        )
        services.add_receipt_line(
            receipt=receipt,
            product=shipped["product"],
            uom=uom(shipped["product"], "PACK"),
            received=15,
            accepted=14,
            rejected=1,
            rejection_reason="Crushed carton",
            batch_number="AMX-EARLY",
            expiry_date=date.today() + timedelta(days=120),
            order_line=shipped["order"].lines.first(),
        )
        services.post_receipt(receipt=receipt, performed_by=shipped["buyer"])

        document = documents.latest(subject=receipt, kind=DocumentKind.GOODS_RECEIPT)
        for heading in ("Ordered", "Received", "Accepted", "Rejected"):
            assert heading in document.html
        assert "Crushed carton" in document.html


class TestInvoiceDocument:
    def test_issuing_an_invoice_renders_it(self, shipped):
        invoice = invoicing.build_invoice(
            order=shipped["order"], kind=InvoiceKind.TAX, performed_by=shipped["seller"]
        )
        invoicing.issue_invoice(invoice=invoice, performed_by=shipped["seller"])

        document = documents.latest(subject=invoice, kind=DocumentKind.TAX_INVOICE)
        assert document is not None
        assert document.number == invoice.number

    def test_a_proforma_says_it_is_not_a_tax_invoice(self, shipped):
        invoice = invoicing.build_invoice(
            order=shipped["order"],
            kind=InvoiceKind.PROFORMA,
            performed_by=shipped["seller"],
        )
        invoicing.issue_invoice(invoice=invoice, performed_by=shipped["seller"])

        document = documents.latest(subject=invoice, kind=DocumentKind.PROFORMA)
        assert "not a tax invoice" in document.html

    def test_money_is_grouped_not_raw_minor_units(self, shipped):
        invoice = invoicing.build_invoice(
            order=shipped["order"], kind=InvoiceKind.TAX, performed_by=shipped["seller"]
        )
        invoicing.issue_invoice(invoice=invoice, performed_by=shipped["seller"])
        document = documents.latest(subject=invoice, kind=DocumentKind.TAX_INVOICE)
        assert "75,000" in document.html


class TestControlledTransferDocument:
    def test_the_form_is_issued_on_dispatch(self, shipped):
        product = make_product(
            shipped["wholesale"], "Morphine 10mg", legal_status=LegalStatus.CONTROLLED
        )
        batch = make_batch(shipped["wholesale"], product, number="MOR-001")
        inventory.post_movement(
            organization=shipped["wholesale"],
            location=shipped["depot"],
            batch=batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(5, uom(product, "PACK")),
        )
        listing = services.publish_listing(
            organization=shipped["wholesale"],
            product=product,
            price=20_000,
            price_uom=uom(product, "PACK"),
            offered_base=Quantity(5, uom(product, "PACK")).base_value,
            performed_by=shipped["seller"],
        )
        order = services.start_order(
            organization=shipped["retail"],
            supplier=shipped["wholesale"],
            deliver_to=shipped["store"],
            performed_by=shipped["buyer"],
        )
        services.add_order_line(order=order, listing=listing, quantity=2)
        services.request_approval(order=order, performed_by=shipped["buyer"])
        services.submit_order(order=order, performed_by=shipped["owner"])
        services.confirm_order(order=order, performed_by=shipped["seller"])

        registration = PharmacistRegistration.objects.create(
            organization=shipped["wholesale"],
            user=shipped["seller"],
            council_number="NPC-4412",
            issued_on=date.today() - timedelta(days=100),
            expiry=date.today() + timedelta(days=300),
        )
        shipment = services.dispatch_order(
            order=order,
            from_location=shipped["depot"],
            performed_by=shipped["seller"],
            controlled_transfer=registration,
        )
        transfer = ControlledTransfer.objects.get(shipment=shipment)
        document = documents.latest(
            subject=transfer, kind=DocumentKind.CONTROLLED_TRANSFER
        )
        assert document is not None
        # The release is attested, not left as an empty line to sign twice.
        assert "Released in Medix" in document.html
        assert "NPC-4412" in document.html
        assert "Morphine 10mg" in document.html


class TestPdfRendering:
    """Playwright is installed and the default backend now uses it.

    A deployment that cannot carry Chromium sets DOCUMENT_PDF_BACKEND to
    "none" and still gets issued, numbered, immutable documents — so
    these skip rather than fail where the browser is absent.
    """

    def test_a_pdf_is_produced(self, shipped):
        from django.conf import settings

        if settings.DOCUMENT_PDF_BACKEND == "none":
            pytest.skip("No PDF backend configured on this host.")

        document = documents.latest(
            subject=shipped["shipment"], kind=DocumentKind.DELIVERY_NOTE
        )
        assert document.pdf, "expected a rendered PDF"
        document.pdf.open("rb")
        try:
            assert document.pdf.read(5) == b"%PDF-"
        finally:
            document.pdf.close()

    def test_the_pdf_is_named_for_its_version(self, shipped):
        from django.conf import settings

        if settings.DOCUMENT_PDF_BACKEND == "none":
            pytest.skip("No PDF backend configured on this host.")

        document = documents.latest(
            subject=shipped["shipment"], kind=DocumentKind.DELIVERY_NOTE
        )
        assert f"{document.number}-v{document.version}" in document.pdf.name

    def test_html_is_stored_whether_or_not_a_pdf_was(self, shipped):
        """The parity guarantee does not depend on the browser."""
        document = documents.latest(
            subject=shipped["shipment"], kind=DocumentKind.DELIVERY_NOTE
        )
        assert document.html
        assert document.sha256
