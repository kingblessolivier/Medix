"""The transfer payload.

The round trip is the point: a product the buyer has never held must
arrive with its packaging chain intact, because every ledger quantity is
stored in base units and a pack that means 100 at the depot and 10 here
would corrupt the received quantity rather than fail loudly.
"""

from datetime import date, timedelta

import pytest

from catalog.models import LegalStatus, Manufacturer, Product, ProductRegistration
from catalog.services import UnidentifiableProduct, ensure_product
from commerce import payloads, services
from commerce.models import GoodsReceipt, GoodsReceiptStatus, TradingRelationship
from core.models import Branch, LicenceKind, LicenceStatus, PremisesLicence, User
from core.quantity import Quantity
from inventory import services as inventory
from inventory.models import MovementKind
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db


def licence(org, kind):
    branch, _ = Branch.objects.get_or_create(
        organization=org, code="MAIN", defaults={"name": "Main"}
    )
    return PremisesLicence.objects.create(
        organization=org,
        branch=branch,
        kind=kind,
        number=f"RFDA-{kind}-{org.name[:4]}",
        issued_on=date.today() - timedelta(days=400),
        expiry=date.today() + timedelta(days=365),
        status=LicenceStatus.ACTIVE,
    )


@pytest.fixture
def shipped():
    wholesale = make_org("ABC Wholesale", kind=LicenceKind.WHOLESALE_PHARMACY)
    retail = make_org("Kigali Care", kind=LicenceKind.RETAIL_PHARMACY)
    licence(wholesale, LicenceKind.WHOLESALE_PHARMACY)
    licence(retail, LicenceKind.RETAIL_PHARMACY)

    seller = User.objects.create_user(username="jean", password="x", organization=wholesale)
    buyer = User.objects.create_user(username="marie", password="x", organization=retail)
    owner = User.objects.create_user(username="claudine", password="x", organization=retail)

    product = make_product(wholesale, "Amoxicillin 500mg", legal_status=LegalStatus.POM)
    product.strength = "500mg"
    product.dosage_form = "CAPSULE"
    product.gtin = "05012345678900"
    product.manufacturer = Manufacturer.objects.create(
        organization=wholesale, name="Cipla", country_of_origin="IN", gmp_certified=True
    )
    product.save()
    ProductRegistration.objects.create(
        organization=wholesale,
        product=product,
        registration_number="RW-FDA-2024-0912",
        strength="500mg",
    )

    depot = make_location(wholesale, "ABC Depot", "DEP")
    store = make_location(retail, "Main Store", "MAIN")

    batch = make_batch(wholesale, product, number="AMX-0021", unit_cost_base=280)
    inventory.post_movement(
        organization=wholesale,
        location=depot,
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        quantity=Quantity(20, uom(product, "PACK")),
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
    services.add_order_line(order=order, listing=listing, quantity=5)
    services.request_approval(order=order, performed_by=buyer)
    services.submit_order(order=order, performed_by=owner)
    services.confirm_order(order=order, performed_by=seller)
    shipment = services.dispatch_order(
        order=order, from_location=depot, performed_by=seller
    )
    return {
        "wholesale": wholesale, "retail": retail, "seller": seller, "buyer": buyer,
        "order": order, "shipment": shipment, "product": product, "store": store,
    }


class TestBuild:
    def test_the_transfer_is_keyed_on_the_delivery_note(self, shipped):
        payload = payloads.build_transfer_payload(shipment=shipped["shipment"])
        assert payload["transfer_id"] == shipped["shipment"].number
        assert payload["schema"] == payloads.SCHEMA

    def test_quantities_are_base_units_only(self, shipped):
        """No pack/loose pair to drift apart."""
        payload = payloads.build_transfer_payload(shipment=shipped["shipment"])
        line = payload["lines"][0]
        assert line["quantity_base"] == 500
        assert "packs_shipped" not in line
        assert "loose_units" not in line

    def test_tax_travels_as_a_treatment_not_a_rate(self, shipped):
        """A frozen rate is wrong the moment the rule changes."""
        payload = payloads.build_transfer_payload(shipment=shipped["shipment"])
        product = payload["lines"][0]["product"]
        assert "tax_treatment" in product
        assert "tax_rate_percentage" not in product

    def test_the_packaging_chain_travels(self, shipped):
        payload = payloads.build_transfer_payload(shipment=shipped["shipment"])
        units = {u["code"]: u["factor_to_base"] for u in payload["lines"][0]["product"]["units"]}
        assert units == {"CARTON": 1200, "PACK": 100, "BLISTER": 10, "UNIT": 1}

    def test_identity_and_manufacturer_travel(self, shipped):
        payload = payloads.build_transfer_payload(shipment=shipped["shipment"])
        product = payload["lines"][0]["product"]
        assert product["registration_number"] == "RW-FDA-2024-0912"
        assert product["gtin"] == "05012345678900"
        assert product["manufacturer"]["country"] == "IN"
        assert product["manufacturer"]["gmp_certified"] is True

    def test_batch_and_cost_travel(self, shipped):
        payload = payloads.build_transfer_payload(shipment=shipped["shipment"])
        line = payload["lines"][0]
        assert line["batch_number"] == "AMX-0021"
        assert line["unit_cost_base"] == 280
        assert line["expiry_date"]


class TestDispatchSeedsTheBuyer:
    def test_a_draft_receipt_appears_for_the_buyer(self, shipped):
        receipt = GoodsReceipt.objects.get(
            organization=shipped["retail"], transfer_id=shipped["shipment"].number
        )
        assert receipt.status == GoodsReceiptStatus.DRAFT

    def test_nothing_entered_the_buyer_ledger(self, shipped):
        """A pre-fill is not a receipt. Someone still counts the cartons."""
        balance = inventory.balance_for(
            organization=shipped["retail"], product=shipped["product"]
        )
        assert balance == 0

    def test_the_buyer_gets_their_own_product_row(self, shipped):
        mine = Product.objects.filter(organization=shipped["retail"])
        assert mine.count() == 1
        assert mine.first().id != shipped["product"].id

    def test_the_mirrored_chain_matches_factor_for_factor(self, shipped):
        mirrored = Product.objects.get(organization=shipped["retail"])
        units = {u.code: u.factor_to_base for u in mirrored.units.all()}
        assert units == {"CARTON": 1200, "PACK": 100, "BLISTER": 10, "UNIT": 1}

    def test_the_prefilled_line_matches_the_delivery_note(self, shipped):
        receipt = GoodsReceipt.objects.get(
            organization=shipped["retail"], transfer_id=shipped["shipment"].number
        )
        line = receipt.lines.get()
        assert line.batch_number == "AMX-0021"
        assert line.received == 5
        assert line.uom.code == "PACK"

    def test_the_receipt_links_back_to_the_order(self, shipped):
        receipt = GoodsReceipt.objects.get(
            organization=shipped["retail"], transfer_id=shipped["shipment"].number
        )
        assert receipt.order_id == shipped["order"].id
        assert receipt.lines.get().order_line_id is not None


class TestIdempotency:
    def test_applying_twice_returns_the_same_receipt(self, shipped):
        payload = payloads.build_transfer_payload(shipment=shipped["shipment"])
        again = payloads.apply_transfer_payload(
            payload=payload,
            organization=shipped["retail"],
            location=shipped["store"],
            performed_by=shipped["buyer"],
            order=shipped["order"],
        )
        assert (
            GoodsReceipt.objects.filter(
                organization=shipped["retail"], transfer_id=shipped["shipment"].number
            ).count()
            == 1
        )
        assert again.transfer_id == shipped["shipment"].number

    def test_the_hash_ignores_key_order(self, shipped):
        payload = payloads.build_transfer_payload(shipment=shipped["shipment"])
        reordered = dict(reversed(list(payload.items())))
        assert payloads.payload_hash(payload) == payloads.payload_hash(reordered)


class TestRefusals:
    def test_an_unknown_schema_is_refused(self, shipped):
        payload = payloads.build_transfer_payload(shipment=shipped["shipment"])
        payload["schema"] = "medix.transfer/99"
        with pytest.raises(payloads.UnsupportedSchema):
            payloads.apply_transfer_payload(
                payload=payload,
                organization=shipped["retail"],
                location=shipped["store"],
                performed_by=shipped["buyer"],
            )

    def test_a_transfer_for_another_pharmacy_is_refused(self, shipped):
        other = make_org("Someone Else", kind=LicenceKind.RETAIL_PHARMACY)
        payload = payloads.build_transfer_payload(shipment=shipped["shipment"])
        with pytest.raises(payloads.WrongRecipient):
            payloads.apply_transfer_payload(
                payload=payload,
                organization=other,
                location=shipped["store"],
                performed_by=shipped["buyer"],
            )

    def test_a_product_with_no_identifier_is_refused_not_name_matched(self, shipped):
        """Name matching would attach a real batch to the wrong product."""
        with pytest.raises(UnidentifiableProduct):
            ensure_product(
                organization=shipped["retail"],
                descriptor={
                    "name": "Amoxicillin 500mg",
                    "units": [{"code": "UNIT", "factor_to_base": 1, "is_base": True}],
                },
                performed_by=shipped["buyer"],
            )


class TestResolution:
    def test_an_existing_registration_number_is_reused(self, shipped):
        """Applying a second delivery must not duplicate the product."""
        mirrored = Product.objects.get(organization=shipped["retail"])
        again = ensure_product(
            organization=shipped["retail"],
            descriptor={
                "registration_number": "RW-FDA-2024-0912",
                "name": "Spelled Differently 500mg",
                "units": [{"code": "UNIT", "factor_to_base": 1, "is_base": True}],
            },
            performed_by=shipped["buyer"],
        )
        assert again.id == mirrored.id
        assert again.name == mirrored.name

    def test_gtin_resolves_when_there_is_no_registration(self, shipped):
        mirrored = Product.objects.get(organization=shipped["retail"])
        again = ensure_product(
            organization=shipped["retail"],
            descriptor={
                "gtin": "05012345678900",
                "name": "Whatever",
                "units": [{"code": "UNIT", "factor_to_base": 1, "is_base": True}],
            },
            performed_by=shipped["buyer"],
        )
        assert again.id == mirrored.id

    def test_a_chain_with_no_base_unit_is_refused(self, shipped):
        from core.exceptions import DomainError

        with pytest.raises(DomainError):
            ensure_product(
                organization=shipped["retail"],
                descriptor={
                    "registration_number": "RW-FDA-9999-0001",
                    "name": "Broken",
                    "units": [{"code": "PACK", "factor_to_base": 100, "is_base": False}],
                },
                performed_by=shipped["buyer"],
            )
