"""Sale completion: prescription gating, controlled register, per-line tax.

Mandatory test groups 5 and 8. See docs/15-testing.md.

Failing open here is not a bug, it is unlawful dispensing — so every gate
is tested from the refusing side as well as the passing one.
"""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from catalog.models import LegalStatus, TaxTreatment
from core.exceptions import PrescriptionRequired, RegistrationInvalid
from core.models import Branch, PharmacistRegistration, User
from core.quantity import Quantity
from inventory import services as inventory
from inventory.models import MovementKind, StockMovement
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom
from sales import payments, services
from sales.models import (
    PaymentMethod,
    ControlledDeliveryEntry,
    Patient,
    Prescription,
    PrescriptionStatus,
    SaleStatus,
    TaxRule,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def counter():
    """A stocked retail pharmacy with a registered pharmacist."""
    org = make_org()
    branch = Branch.objects.create(organization=org, name="Kigali Main", code="KGL")
    location = make_location(org)

    cashier = User.objects.create_user(username="cashier", password="x", organization=org)
    pharmacist = User.objects.create_user(
        username="marie", password="x", organization=org, first_name="Marie"
    )
    PharmacistRegistration.objects.create(
        organization=org,
        user=pharmacist,
        council_number="NPC-00214",
        issued_on=date.today() - timedelta(days=365),
        expiry=date.today() + timedelta(days=365),
    )

    TaxRule.objects.create(
        organization=org,
        treatment=TaxTreatment.STANDARD,
        rate_basis_points=1800,
        effective_from=date(2020, 1, 1),
    )

    def stock(product, packs=5, number="B-0001"):
        batch = make_batch(org, product, number=number)
        inventory.post_movement(
            organization=org,
            location=location,
            batch=batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(packs, uom(product, "PACK")),
        )
        return batch

    return {
        "org": org,
        "branch": branch,
        "location": location,
        "cashier": cashier,
        "pharmacist": pharmacist,
        "stock": stock,
    }


def new_sale(counter):
    return services.start_sale(
        organization=counter["org"],
        branch=counter["branch"],
        location=counter["location"],
        cashier=counter["cashier"],
    )


def verified_prescription(counter, *, address="KG 11 Ave, Kigali") -> Prescription:
    patient = Patient.objects.create(
        organization=counter["org"], full_name="J. Mukamana", address=address
    )
    prescription = Prescription.objects.create(
        organization=counter["org"], patient=patient, number="RX-001"
    )
    return services.verify_prescription(
        prescription=prescription, pharmacist=counter["pharmacist"]
    )


# --------------------------------------------------------------------------


class TestOverTheCounter:
    def test_otc_sale_completes_without_a_pharmacist(self, counter):
        product = make_product(counter["org"], "Paracetamol 500mg", legal_status=LegalStatus.OTC)
        counter["stock"](product)

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=product, quantity=6, uom=uom(product, "UNIT"), unit_price=120
        )
        completed = services.complete_sale(sale=sale, performed_by=counter["cashier"])

        # Goods have left; nothing is tendered yet.
        assert completed.status == SaleStatus.PENDING_PAYMENT
        assert completed.number.startswith("SAL-")
        assert completed.total == 720

        payments.take_payment(
            sale=completed, method=PaymentMethod.CASH, amount=720,
            performed_by=counter["cashier"],
        )
        completed.refresh_from_db()
        assert completed.status == SaleStatus.COMPLETED

    def test_stock_leaves_through_the_ledger(self, counter):
        product = make_product(counter["org"], "Paracetamol 500mg", legal_status=LegalStatus.OTC)
        counter["stock"](product)

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=product, quantity=6, uom=uom(product, "UNIT"), unit_price=120
        )
        services.complete_sale(sale=sale, performed_by=counter["cashier"])

        movement = StockMovement.objects.get(kind=MovementKind.SALE)
        assert movement.quantity_base == -6
        assert (
            inventory.balance_for(organization=counter["org"], product=product) == 494
        )

    def test_empty_sale_refused(self, counter):
        with pytest.raises(services.EmptySale):
            services.complete_sale(sale=new_sale(counter), performed_by=counter["cashier"])


class TestPrescriptionGating:
    """A POM line blocks. Not a warning."""

    def test_pom_without_prescription_is_blocked(self, counter):
        product = make_product(counter["org"], legal_status=LegalStatus.POM)
        counter["stock"](product)

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=product, quantity=6, uom=uom(product, "UNIT"), unit_price=280
        )

        with pytest.raises(PrescriptionRequired) as exc:
            services.complete_sale(sale=sale, performed_by=counter["cashier"])
        assert "Amoxicillin 500mg" in str(exc.value)

    def test_unverified_prescription_is_blocked(self, counter):
        product = make_product(counter["org"], legal_status=LegalStatus.POM)
        counter["stock"](product)
        patient = Patient.objects.create(organization=counter["org"], full_name="J. Mukamana")
        unverified = Prescription.objects.create(
            organization=counter["org"], patient=patient, status=PrescriptionStatus.PENDING
        )

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=product, quantity=6, uom=uom(product, "UNIT"), unit_price=280
        )

        with pytest.raises(PrescriptionRequired):
            services.complete_sale(
                sale=sale,
                performed_by=counter["cashier"],
                pharmacist=counter["pharmacist"],
                prescription=unverified,
            )

    def test_ocr_extract_alone_does_not_authorize(self, counter):
        """OCR reads; a pharmacist authorizes."""
        product = make_product(counter["org"], legal_status=LegalStatus.POM)
        counter["stock"](product)
        patient = Patient.objects.create(organization=counter["org"], full_name="J. Mukamana")
        scanned = Prescription.objects.create(
            organization=counter["org"],
            patient=patient,
            ocr_extract={"product": "Amoxicillin 500mg", "quantity": 20},
            status=PrescriptionStatus.PENDING,
        )

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=product, quantity=6, uom=uom(product, "UNIT"), unit_price=280
        )

        with pytest.raises(PrescriptionRequired):
            services.complete_sale(
                sale=sale,
                performed_by=counter["cashier"],
                pharmacist=counter["pharmacist"],
                prescription=scanned,
            )

    def test_pom_without_a_pharmacist_is_blocked(self, counter):
        product = make_product(counter["org"], legal_status=LegalStatus.POM)
        counter["stock"](product)
        prescription = verified_prescription(counter)

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=product, quantity=6, uom=uom(product, "UNIT"), unit_price=280
        )

        with pytest.raises(PrescriptionRequired):
            services.complete_sale(
                sale=sale, performed_by=counter["cashier"], prescription=prescription
            )

    def test_expired_registration_cannot_complete(self, counter):
        """Registration expiry revokes capability automatically."""
        PharmacistRegistration.objects.filter(user=counter["pharmacist"]).update(
            expiry=date.today() - timedelta(days=1)
        )
        product = make_product(counter["org"], legal_status=LegalStatus.POM)
        counter["stock"](product)

        patient = Patient.objects.create(organization=counter["org"], full_name="J. M.")
        prescription = Prescription.objects.create(
            organization=counter["org"], patient=patient, status=PrescriptionStatus.VERIFIED
        )

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=product, quantity=6, uom=uom(product, "UNIT"), unit_price=280
        )

        with pytest.raises(RegistrationInvalid):
            services.complete_sale(
                sale=sale,
                performed_by=counter["cashier"],
                pharmacist=counter["pharmacist"],
                prescription=prescription,
            )

    def test_verified_prescription_completes(self, counter):
        product = make_product(counter["org"], legal_status=LegalStatus.POM)
        counter["stock"](product)
        prescription = verified_prescription(counter)

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=product, quantity=6, uom=uom(product, "UNIT"), unit_price=280
        )
        completed = services.complete_sale(
            sale=sale,
            performed_by=counter["cashier"],
            pharmacist=counter["pharmacist"],
            prescription=prescription,
        )

        assert completed.status == SaleStatus.PENDING_PAYMENT
        assert completed.pharmacist == counter["pharmacist"]

    def test_nothing_moves_when_a_gate_refuses(self, counter):
        product = make_product(counter["org"], legal_status=LegalStatus.POM)
        counter["stock"](product)
        before = inventory.balance_for(organization=counter["org"], product=product)

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=product, quantity=6, uom=uom(product, "UNIT"), unit_price=280
        )
        with pytest.raises(PrescriptionRequired):
            services.complete_sale(sale=sale, performed_by=counter["cashier"])

        assert inventory.balance_for(organization=counter["org"], product=product) == before
        assert not StockMovement.objects.filter(kind=MovementKind.SALE).exists()


class TestPharmacistVerification:
    def test_verify_records_the_council_number(self, counter):
        prescription = verified_prescription(counter)
        assert prescription.verified_by_council_number == "NPC-00214"
        assert prescription.verified_by == counter["pharmacist"]

    def test_unregistered_user_cannot_verify(self, counter):
        patient = Patient.objects.create(organization=counter["org"], full_name="J. M.")
        prescription = Prescription.objects.create(
            organization=counter["org"], patient=patient
        )
        with pytest.raises(RegistrationInvalid):
            services.verify_prescription(
                prescription=prescription, pharmacist=counter["cashier"]
            )


class TestControlledRegister:
    """Law n° 03/2012 — a statutory record, not a flag."""

    def _controlled(self, counter):
        product = make_product(
            counter["org"], "Morphine 10mg", legal_status=LegalStatus.CONTROLLED
        )
        counter["stock"](product, number="MOR-0001")
        return product

    def test_entry_written_with_patient_name_and_address(self, counter):
        product = self._controlled(counter)
        prescription = verified_prescription(counter)

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=product, quantity=10, uom=uom(product, "UNIT"), unit_price=900
        )
        services.complete_sale(
            sale=sale,
            performed_by=counter["cashier"],
            pharmacist=counter["pharmacist"],
            prescription=prescription,
        )

        entry = ControlledDeliveryEntry.objects.get()
        assert entry.patient_name == "J. Mukamana"
        assert entry.patient_address == "KG 11 Ave, Kigali"
        assert entry.substance_denomination == "Morphine 10mg"
        assert entry.dispensed_by_council_number == "NPC-00214"
        assert entry.quantity_base == 10

    def test_blocked_without_a_patient_address(self, counter):
        product = self._controlled(counter)
        prescription = verified_prescription(counter, address="")

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=product, quantity=10, uom=uom(product, "UNIT"), unit_price=900
        )

        with pytest.raises(services.PatientAddressRequired):
            services.complete_sale(
                sale=sale,
                performed_by=counter["cashier"],
                pharmacist=counter["pharmacist"],
                prescription=prescription,
            )

    def test_exactly_one_entry_per_controlled_line(self, counter):
        product = self._controlled(counter)
        prescription = verified_prescription(counter)

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=product, quantity=10, uom=uom(product, "UNIT"), unit_price=900
        )
        services.complete_sale(
            sale=sale,
            performed_by=counter["cashier"],
            pharmacist=counter["pharmacist"],
            prescription=prescription,
        )
        assert ControlledDeliveryEntry.objects.count() == 1

    def test_no_entry_for_a_non_controlled_sale(self, counter):
        product = make_product(counter["org"], "Paracetamol", legal_status=LegalStatus.OTC)
        counter["stock"](product)

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=product, quantity=6, uom=uom(product, "UNIT"), unit_price=120
        )
        services.complete_sale(sale=sale, performed_by=counter["cashier"])
        assert ControlledDeliveryEntry.objects.count() == 0

    def test_register_is_append_only(self, counter):
        product = self._controlled(counter)
        prescription = verified_prescription(counter)
        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=product, quantity=10, uom=uom(product, "UNIT"), unit_price=900
        )
        services.complete_sale(
            sale=sale,
            performed_by=counter["cashier"],
            pharmacist=counter["pharmacist"],
            prescription=prescription,
        )

        entry = ControlledDeliveryEntry.objects.get()
        entry.quantity_base = 1
        with pytest.raises(RuntimeError, match="append-only"):
            entry.save()
        with pytest.raises(RuntimeError, match="append-only"):
            entry.delete()

    def test_running_balance_decrements(self, counter):
        product = self._controlled(counter)
        prescription = verified_prescription(counter)

        for _ in range(2):
            sale = new_sale(counter)
            services.add_line(
                sale=sale, product=product, quantity=10, uom=uom(product, "UNIT"), unit_price=900
            )
            services.complete_sale(
                sale=sale,
                performed_by=counter["cashier"],
                pharmacist=counter["pharmacist"],
                prescription=prescription,
            )

        balances = list(
            ControlledDeliveryEntry.objects.order_by("entered_at").values_list(
                "balance_after_base", flat=True
            )
        )
        assert balances == [-10, -20]


class TestTax:
    """Per line, against rules effective on the sale date."""

    def test_exempt_medicine_carries_no_tax(self, counter):
        product = make_product(
            counter["org"], legal_status=LegalStatus.OTC, tax=TaxTreatment.EXEMPT
        )
        counter["stock"](product)

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=product, quantity=10, uom=uom(product, "UNIT"), unit_price=100
        )
        assert sale.tax_total == 0
        assert sale.total == 1000

    def test_standard_rated_item_is_taxed(self, counter):
        gloves = make_product(
            counter["org"], "Surgical gloves", legal_status=LegalStatus.OTC,
            tax=TaxTreatment.STANDARD,
        )
        counter["stock"](gloves, number="GLV-0001")

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=gloves, quantity=10, uom=uom(gloves, "UNIT"), unit_price=100
        )
        assert sale.tax_total == 180
        assert sale.total == 1180

    def test_mixed_basket_carries_two_treatments(self, counter):
        """The normal pharmacy basket, not an edge case."""
        medicine = make_product(
            counter["org"], "Paracetamol", legal_status=LegalStatus.OTC, tax=TaxTreatment.EXEMPT
        )
        gloves = make_product(
            counter["org"], "Surgical gloves", legal_status=LegalStatus.OTC,
            tax=TaxTreatment.STANDARD,
        )
        counter["stock"](medicine, number="PCM-0001")
        counter["stock"](gloves, number="GLV-0001")

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=medicine, quantity=10, uom=uom(medicine, "UNIT"), unit_price=100
        )
        services.add_line(
            sale=sale, product=gloves, quantity=10, uom=uom(gloves, "UNIT"), unit_price=100
        )

        treatments = set(sale.lines.values_list("tax_treatment", flat=True))
        assert treatments == {"EXEMPT", "STANDARD"}
        assert sale.tax_total == 180
        assert sale.total == 2180

    def test_exempt_is_not_zero_rated(self, counter):
        """Both charge nothing; only one allows input VAT recovery, so the
        treatment is recorded on the line and not merely the rate."""
        exempt = make_product(
            counter["org"], "Medicine", legal_status=LegalStatus.OTC, tax=TaxTreatment.EXEMPT
        )
        zero = make_product(
            counter["org"], "Export item", legal_status=LegalStatus.OTC,
            tax=TaxTreatment.ZERO_RATED,
        )
        counter["stock"](exempt, number="EX-0001")
        counter["stock"](zero, number="ZR-0001")

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=exempt, quantity=1, uom=uom(exempt, "UNIT"), unit_price=100
        )
        services.add_line(
            sale=sale, product=zero, quantity=1, uom=uom(zero, "UNIT"), unit_price=100
        )

        rows = {line.tax_treatment: line.tax_amount for line in sale.lines.all()}
        assert rows == {"EXEMPT": 0, "ZERO": 0}

    def test_uses_the_rule_effective_on_the_sale_date(self, counter):
        """A superseded rule must not be applied to a current sale."""
        TaxRule.objects.filter(organization=counter["org"]).update(
            effective_to=date(2024, 12, 31)
        )
        TaxRule.objects.create(
            organization=counter["org"],
            treatment=TaxTreatment.STANDARD,
            rate_basis_points=2000,
            effective_from=date(2025, 1, 1),
        )
        gloves = make_product(
            counter["org"], "Surgical gloves", legal_status=LegalStatus.OTC,
            tax=TaxTreatment.STANDARD,
        )
        counter["stock"](gloves, number="GLV-0001")

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=gloves, quantity=10, uom=uom(gloves, "UNIT"), unit_price=100
        )
        assert sale.lines.get().tax_rate_basis_points == 2000
        assert sale.tax_total == 200


class TestBatchAllocation:
    def test_line_records_the_batch_and_its_cost(self, counter):
        """Margin comes from the batch, so the line must carry its cost."""
        product = make_product(counter["org"], legal_status=LegalStatus.OTC)
        batch = counter["stock"](product)

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=product, quantity=6, uom=uom(product, "UNIT"), unit_price=500
        )
        line = sale.lines.get()
        assert line.batch == batch
        assert line.unit_cost_base == batch.unit_cost_base

    def test_a_span_across_batches_becomes_two_lines(self, counter):
        """Each batch keeps its own cost and traceability."""
        product = make_product(counter["org"], legal_status=LegalStatus.OTC)
        near = make_batch(counter["org"], product, number="NEAR", expires_in_days=30)
        far = make_batch(counter["org"], product, number="FAR", expires_in_days=900)
        for batch, packs in [(near, 1), (far, 5)]:
            inventory.post_movement(
                organization=counter["org"],
                location=counter["location"],
                batch=batch,
                kind=MovementKind.PURCHASE_RECEIPT,
                quantity=Quantity(packs, uom(product, "PACK")),
            )

        sale = new_sale(counter)
        lines = services.add_line(
            sale=sale, product=product, quantity=150, uom=uom(product, "UNIT"), unit_price=10
        )

        assert len(lines) == 2
        assert [line.batch.batch_number for line in lines] == ["NEAR", "FAR"]
        assert [line.quantity_base for line in lines] == [100, 50]

    def test_unsellable_uom_refused(self, counter):
        """A product may forbid partial-pack dispensing."""
        product = make_product(counter["org"], legal_status=LegalStatus.OTC)
        counter["stock"](product)
        unit = uom(product, "UNIT")
        unit.is_sellable = False
        unit.save(update_fields=["is_sellable"])

        sale = new_sale(counter)
        with pytest.raises(services.NotSellable):
            services.add_line(
                sale=sale, product=product, quantity=6, uom=unit, unit_price=100
            )


class TestIdempotency:
    def test_completing_twice_is_a_no_op(self, counter):
        product = make_product(counter["org"], legal_status=LegalStatus.OTC)
        counter["stock"](product)

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=product, quantity=6, uom=uom(product, "UNIT"), unit_price=120
        )
        first = services.complete_sale(sale=sale, performed_by=counter["cashier"])
        second = services.complete_sale(sale=sale, performed_by=counter["cashier"])

        assert first.number == second.number
        assert StockMovement.objects.filter(kind=MovementKind.SALE).count() == 1

    def test_a_completed_sale_cannot_take_new_lines(self, counter):
        product = make_product(counter["org"], legal_status=LegalStatus.OTC)
        counter["stock"](product)

        sale = new_sale(counter)
        services.add_line(
            sale=sale, product=product, quantity=6, uom=uom(product, "UNIT"), unit_price=120
        )
        services.complete_sale(sale=sale, performed_by=counter["cashier"])

        with pytest.raises(services.SaleNotDraft):
            services.add_line(
                sale=sale, product=product, quantity=1, uom=uom(product, "UNIT"), unit_price=120
            )
