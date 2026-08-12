"""Import documents, the CoA gate and cold-chain quarantine.

The two gates are the point of this stage. Filing is filing; a
Certificate of Analysis releasing a batch and a temperature breach
holding one are the parts that change what stock a pharmacy can sell.
"""

from datetime import date, timedelta

import pytest

from commerce import services
from commerce.models import (
    ImportDocument,
    ImportDocumentKind,
)
from catalog.models import ProductRegistration
from core.exceptions import DomainError
from core.models import Branch, LicenceKind, LicenceStatus, PremisesLicence, User
from inventory import services as inventory
from inventory.models import StockStatus
from inventory.tests.factories import make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db

TODAY = date.today()
FUTURE = TODAY + timedelta(days=400)


@pytest.fixture
def importer():
    org = make_org("ABC Importers", kind=LicenceKind.IMPORTER)
    branch, _ = Branch.objects.get_or_create(
        organization=org, code="MAIN", defaults={"name": "Main"}
    )
    PremisesLicence.objects.create(
        organization=org,
        branch=branch,
        kind=LicenceKind.IMPORTER,
        number="RFDA-IMP-ABC",
        issued_on=TODAY - timedelta(days=400),
        expiry=TODAY + timedelta(days=365),
        status=LicenceStatus.ACTIVE,
    )
    user = User.objects.create_user(username="jean", password="x", organization=org)
    product = make_product(org, "Amoxicillin 500mg")
    # Registered, so it is the kind of product a CoA covers.
    ProductRegistration.objects.create(
        organization=org,
        product=product,
        registration_number="RW-FDA-2024-0912",
    )
    warehouse = make_location(org, "Depot", "DEP")
    return {"org": org, "user": user, "product": product, "warehouse": warehouse}


def import_receipt(importer, *, cold_chain_ok=None, freight=0):
    """A foreign consignment: invoiced in USD and carrying freight."""
    receipt = services.start_receipt(
        organization=importer["org"],
        location=importer["warehouse"],
        performed_by=importer["user"],
    )
    receipt.invoice_currency = "USD"
    receipt.fx_rate_scaled = 13_205_000
    receipt.freight = freight
    receipt.transport_temperature_ok = cold_chain_ok
    receipt.save(
        update_fields=[
            "invoice_currency", "fx_rate_scaled", "freight", "transport_temperature_ok"
        ]
    )
    services.add_receipt_line(
        receipt=receipt,
        product=importer["product"],
        uom=uom(importer["product"], "PACK"),
        received=10,
        batch_number="IMP-001",
        expiry_date=FUTURE,
        unit_cost_base=3,
    )
    return receipt


def coa(importer, receipt, *, batch=None):
    return ImportDocument.objects.create(
        organization=importer["org"],
        receipt=receipt,
        batch=batch,
        kind=ImportDocumentKind.CERTIFICATE_OF_ANALYSIS,
        number="COA-2026-11",
        issued_by="Cipla QA",
        issued_on=TODAY - timedelta(days=30),
    )


def cold_chain_log(importer, receipt, *, breach):
    return ImportDocument.objects.create(
        organization=importer["org"],
        receipt=receipt,
        kind=ImportDocumentKind.COLD_CHAIN_LOG,
        number="LOG-1",
        min_temperature_c=1.4 if breach else 2.6,
        max_temperature_c=11.2 if breach else 7.4,
        breach=breach,
    )


def held(importer):
    return inventory.balance_for(
        organization=importer["org"],
        product=importer["product"],
        status=StockStatus.QUARANTINED,
    )


def available(importer):
    return inventory.balance_for(
        organization=importer["org"], product=importer["product"]
    )


class TestCertificateOfAnalysisGate:
    def test_an_import_without_a_coa_quarantines(self, importer):
        """A batch nobody has tested is not stock, it is a liability."""
        receipt = import_receipt(importer, freight=50_000)
        services.post_receipt(receipt=receipt, performed_by=importer["user"])

        assert held(importer) == 1_000
        assert available(importer) == 0

    def test_a_coa_on_file_releases_it(self, importer):
        receipt = import_receipt(importer, freight=50_000)
        coa(importer, receipt)
        services.post_receipt(receipt=receipt, performed_by=importer["user"])

        assert available(importer) == 1_000
        assert held(importer) == 0

    def test_a_domestic_delivery_needs_no_certificate(self, importer):
        """A local depot ships under its own release, not a manufacturer's."""
        receipt = services.start_receipt(
            organization=importer["org"],
            location=importer["warehouse"],
            performed_by=importer["user"],
        )
        services.add_receipt_line(
            receipt=receipt,
            product=importer["product"],
            uom=uom(importer["product"], "PACK"),
            received=10,
            batch_number="LOCAL-1",
            expiry_date=FUTURE,
            unit_cost_base=300,
        )
        services.post_receipt(receipt=receipt, performed_by=importer["user"])
        assert available(importer) == 1_000

    def test_an_unregistered_product_needs_no_certificate(self, importer):
        """Holding every box of plasters would empty the shelves."""
        plasters = make_product(importer["org"], "Adhesive plasters")
        receipt = services.start_receipt(
            organization=importer["org"],
            location=importer["warehouse"],
            performed_by=importer["user"],
        )
        receipt.freight = 10_000
        receipt.save(update_fields=["freight"])
        services.add_receipt_line(
            receipt=receipt,
            product=plasters,
            uom=uom(plasters, "PACK"),
            received=5,
            batch_number="PLA-1",
            expiry_date=FUTURE,
            unit_cost_base=100,
        )
        services.post_receipt(receipt=receipt, performed_by=importer["user"])
        assert (
            inventory.balance_for(organization=importer["org"], product=plasters) == 500
        )


class TestColdChainQuarantine:
    def test_a_recorded_breach_quarantines(self, importer):
        """Not a warning: by the time anyone reads one it is already damaged."""
        receipt = import_receipt(importer, freight=50_000)
        coa(importer, receipt)
        cold_chain_log(importer, receipt, breach=True)
        services.post_receipt(receipt=receipt, performed_by=importer["user"])

        assert held(importer) == 1_000
        assert available(importer) == 0

    def test_a_clean_log_does_not_hold_it(self, importer):
        receipt = import_receipt(importer, freight=50_000)
        coa(importer, receipt)
        cold_chain_log(importer, receipt, breach=False)
        services.post_receipt(receipt=receipt, performed_by=importer["user"])

        assert available(importer) == 1_000

    def test_a_breach_outranks_a_certificate(self, importer):
        """A tested batch that arrived warm is still a damaged batch."""
        receipt = import_receipt(importer, freight=50_000)
        coa(importer, receipt)
        cold_chain_log(importer, receipt, breach=True)
        services.post_receipt(receipt=receipt, performed_by=importer["user"])

        movement = (
            importer["product"]
            .batches.get(batch_number="IMP-001")
            .movements.first()
        )
        assert "excursion" in movement.reason.lower()


class TestRelease:
    def test_released_stock_becomes_available(self, importer):
        receipt = import_receipt(importer, freight=50_000)
        services.post_receipt(receipt=receipt, performed_by=importer["user"])
        batch = importer["product"].batches.get(batch_number="IMP-001")

        services.release_batch(
            batch=batch,
            location=importer["warehouse"],
            organization=importer["org"],
            performed_by=importer["user"],
            reason="Certificate received by email, filed.",
        )
        assert available(importer) == 1_000
        assert held(importer) == 0

    def test_the_history_of_being_held_survives(self, importer):
        """Two movements, not a status edit — the ledger is the record."""
        receipt = import_receipt(importer, freight=50_000)
        services.post_receipt(receipt=receipt, performed_by=importer["user"])
        batch = importer["product"].batches.get(batch_number="IMP-001")

        before = batch.movements.count()
        services.release_batch(
            batch=batch,
            location=importer["warehouse"],
            organization=importer["org"],
            performed_by=importer["user"],
            reason="Certificate received.",
        )
        assert batch.movements.count() == before + 2

    def test_a_release_without_a_reason_is_refused(self, importer):
        receipt = import_receipt(importer, freight=50_000)
        services.post_receipt(receipt=receipt, performed_by=importer["user"])
        batch = importer["product"].batches.get(batch_number="IMP-001")

        with pytest.raises(DomainError):
            services.release_batch(
                batch=batch,
                location=importer["warehouse"],
                organization=importer["org"],
                performed_by=importer["user"],
                reason="   ",
            )

    def test_releasing_nothing_is_refused(self, importer):
        receipt = import_receipt(importer, freight=50_000)
        coa(importer, receipt)
        services.post_receipt(receipt=receipt, performed_by=importer["user"])
        batch = importer["product"].batches.get(batch_number="IMP-001")

        with pytest.raises(DomainError):
            services.release_batch(
                batch=batch,
                location=importer["warehouse"],
                organization=importer["org"],
                performed_by=importer["user"],
                reason="Nothing to release.",
            )

    def test_the_release_is_audited(self, importer):
        from core.models import AuditEvent

        receipt = import_receipt(importer, freight=50_000)
        services.post_receipt(receipt=receipt, performed_by=importer["user"])
        batch = importer["product"].batches.get(batch_number="IMP-001")

        services.release_batch(
            batch=batch,
            location=importer["warehouse"],
            organization=importer["org"],
            performed_by=importer["user"],
            reason="Certificate received.",
        )
        assert AuditEvent.objects.filter(action="inventory.batch.released").exists()


class TestFiling:
    def test_documents_attach_to_the_receipt(self, importer):
        receipt = import_receipt(importer)
        for kind in (
            ImportDocumentKind.IMPORT_LICENCE,
            ImportDocumentKind.COMMERCIAL_INVOICE,
            ImportDocumentKind.PACKING_LIST,
            ImportDocumentKind.BILL_OF_LADING,
            ImportDocumentKind.CERTIFICATE_OF_ORIGIN,
            ImportDocumentKind.CUSTOMS_DECLARATION,
        ):
            ImportDocument.objects.create(
                organization=importer["org"], receipt=receipt, kind=kind, number="X"
            )
        assert receipt.import_documents.count() == 6

    def test_a_coa_can_be_filed_against_one_batch(self, importer):
        """Consignment-level filing loses what a recall needs."""
        receipt = import_receipt(importer, freight=50_000)
        services.post_receipt(receipt=receipt, performed_by=importer["user"])
        batch = importer["product"].batches.get(batch_number="IMP-001")

        document = coa(importer, receipt, batch=batch)
        assert document.batch_id == batch.id

    def test_verification_is_separate_from_upload(self, importer):
        """Holding a file is not the same as having checked it."""
        receipt = import_receipt(importer)
        document = coa(importer, receipt)
        assert not document.is_verified
