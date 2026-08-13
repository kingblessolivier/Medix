"""Catalog: typed attributes and UoM chain integrity."""

import pytest

from catalog import services
from catalog.models import (
    AttributeDataType,
    AttributeDefinition,
    LegalStatus,
    ProductType,
    ProductTypeCode,
    TaxTreatment,
    UnitOfMeasure,
)
from core.exceptions import DomainError
from inventory.tests.factories import make_org, make_product

pytestmark = pytest.mark.django_db


@pytest.fixture
def medicine_type():
    org = make_org()
    product_type = ProductType.objects.create(
        organization=org, code=ProductTypeCode.MEDICINE, name="Medicine"
    )
    AttributeDefinition.objects.create(
        organization=org,
        product_type=product_type,
        code="strength",
        label="Strength",
        data_type=AttributeDataType.TEXT,
        required=True,
        group="Pharmaceutical",
    )
    AttributeDefinition.objects.create(
        organization=org,
        product_type=product_type,
        code="pack_size",
        label="Pack size",
        data_type=AttributeDataType.NUMBER,
        group="Pharmaceutical",
    )
    AttributeDefinition.objects.create(
        organization=org,
        product_type=product_type,
        code="route",
        label="Route",
        data_type=AttributeDataType.ENUM,
        enum_values=["Oral", "Topical", "Injection"],
        group="Pharmaceutical",
    )
    return org, product_type


class TestAttributeValidation:
    def test_accepts_valid(self, medicine_type):
        _, product_type = medicine_type
        result = services.validate_attributes(
            product_type, {"strength": "500mg", "pack_size": 100, "route": "Oral"}
        )
        assert result["strength"] == "500mg"

    def test_rejects_unknown_attribute(self, medicine_type):
        _, product_type = medicine_type
        with pytest.raises(DomainError) as exc:
            services.validate_attributes(product_type, {"strength": "500mg", "colour": "blue"})
        assert "colour" in exc.value.meta["unknown"]

    def test_rejects_missing_required(self, medicine_type):
        _, product_type = medicine_type
        with pytest.raises(DomainError) as exc:
            services.validate_attributes(product_type, {"pack_size": 100})
        assert "strength" in exc.value.meta["missing"]

    def test_rejects_wrong_type(self, medicine_type):
        _, product_type = medicine_type
        with pytest.raises(DomainError):
            services.validate_attributes(
                product_type, {"strength": "500mg", "pack_size": "one hundred"}
            )

    def test_rejects_value_outside_enum(self, medicine_type):
        _, product_type = medicine_type
        with pytest.raises(DomainError):
            services.validate_attributes(
                product_type, {"strength": "500mg", "route": "Intravenous"}
            )

    def test_attributes_group_for_the_form(self, medicine_type):
        """Groups drive form sections, never one flat wall of inputs."""
        _, product_type = medicine_type
        groups = set(
            AttributeDefinition.objects.filter(product_type=product_type).values_list(
                "group", flat=True
            )
        )
        assert groups == {"Pharmaceutical"}


class TestUomChain:
    def test_valid_chain_passes(self):
        org = make_org()
        product = make_product(org)
        services.validate_uom_chain(product)

    def test_base_unit_is_factor_one(self):
        org = make_org()
        product = make_product(org)
        assert product.base_uom.factor_to_base == 1
        assert product.base_uom.code == "UNIT"

    def test_rejects_duplicate_factors(self):
        org = make_org()
        product = make_product(org)
        UnitOfMeasure.objects.create(
            organization=org,
            product=product,
            code="STRIP",
            name="Strip of 10",
            factor_to_base=10,
        )
        with pytest.raises(DomainError, match="conversion factor"):
            services.validate_uom_chain(product)

    def test_rejects_chain_without_units(self):
        org = make_org()
        product = make_product(org)
        UnitOfMeasure.objects.filter(product=product).delete()
        with pytest.raises(DomainError, match="no unit of measure"):
            services.validate_uom_chain(product)


class TestLegalStatus:
    def test_pom_requires_prescription(self):
        org = make_org()
        product = make_product(org, legal_status=LegalStatus.POM)
        assert product.requires_prescription is True

    def test_otc_does_not(self):
        org = make_org()
        product = make_product(org, "Paracetamol 500mg", legal_status=LegalStatus.OTC)
        assert product.requires_prescription is False

    def test_controlled_requires_prescription(self):
        org = make_org()
        product = make_product(org, "Morphine 10mg", legal_status=LegalStatus.CONTROLLED)
        assert product.requires_prescription is True


class TestTaxTreatment:
    def test_exempt_is_distinct_from_zero_rated(self):
        """Input VAT on exempt supplies is not reclaimable. The distinction
        changes true cost of goods and therefore margin."""
        assert TaxTreatment.EXEMPT != TaxTreatment.ZERO_RATED

    def test_medicine_defaults_exempt_in_fixtures(self):
        org = make_org()
        product = make_product(org)
        assert product.tax_treatment == TaxTreatment.EXEMPT


class TestTheTillCanSeeTheShelf:
    """The product list carries what is on hand, because the till reads it.

    A cashier searching "paracetamol" gets several products that look
    alike. Which of them can actually be sold is the thing that separates
    them, and finding that out by tapping one and reading a red banner is
    a conversation with the patient that should not have to happen.
    """

    @pytest.fixture
    def counter(self, db):
        from core.models import User
        from core.quantity import Quantity
        from inventory import services
        from inventory.models import MovementKind, StockStatus
        from inventory.tests.factories import make_batch, make_location, make_product, uom
        from rest_framework.test import APIClient

        org = make_org("Kigali Care")
        user = User.objects.create_user(username="marie", password="x", organization=org)
        location = make_location(org)

        stocked = make_product(org, "Paracetamol 500mg tablets")
        empty = make_product(org, "Paracetamol 120mg/5ml syrup")
        held = make_product(org, "Paracetamol 250mg suppository")

        batch = make_batch(org, stocked, number="PAR-1")
        services.post_movement(
            organization=org, location=location, batch=batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(3, uom(stocked, "PACK")),
        )

        # Quarantined stock is not sellable, so it must not be counted.
        held_batch = make_batch(org, held, number="PAR-3")
        services.post_movement(
            organization=org, location=location, batch=held_batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(5, uom(held, "PACK")),
            status=StockStatus.QUARANTINED,
        )

        client = APIClient()
        client.force_authenticate(user=user)
        return {"client": client, "stocked": stocked, "empty": empty, "held": held}

    def rows(self, counter):
        response = counter["client"].get("/api/v1/products/?search=paracetamol")
        assert response.status_code == 200
        return {row["name"]: row for row in response.data["results"]}

    def test_a_stocked_product_says_how_much(self, counter):
        rows = self.rows(counter)
        assert rows["Paracetamol 500mg tablets"]["on_hand_base"] == 300

    def test_a_product_with_none_says_zero(self, counter):
        """Not absent from the list — a cashier still needs to find it."""
        rows = self.rows(counter)
        assert rows["Paracetamol 120mg/5ml syrup"]["on_hand_base"] == 0

    def test_quarantined_stock_does_not_count(self, counter):
        """It is on the premises and it is not sellable."""
        rows = self.rows(counter)
        assert rows["Paracetamol 250mg suppository"]["on_hand_base"] == 0

    def test_the_figure_names_its_unit(self, counter):
        """"300" is not an answer. Three hundred of what?"""
        rows = self.rows(counter)
        assert rows["Paracetamol 500mg tablets"]["base_uom_name"]

    def test_a_location_scopes_the_figure(self, counter):
        """The till sells from one room, so it asks about that room.

        A front counter told there are twenty-four bottles when all
        twenty-four are in the cold room is back where it started.
        """
        from inventory.tests.factories import make_location

        elsewhere = make_location(counter["stocked"].organization, "Cold room", "COLD")
        response = counter["client"].get(
            f"/api/v1/products/?search=paracetamol&location={elsewhere.id}"
        )
        rows = {row["name"]: row for row in response.data["results"]}
        assert rows["Paracetamol 500mg tablets"]["on_hand_base"] == 0

    def test_without_a_location_it_is_the_whole_pharmacy(self, counter):
        rows = self.rows(counter)
        assert rows["Paracetamol 500mg tablets"]["on_hand_base"] == 300

    def test_the_list_does_not_cost_a_query_per_row(self, counter, django_assert_max_num_queries):
        """The till searches on every keystroke."""
        with django_assert_max_num_queries(10):
            counter["client"].get("/api/v1/products/?search=paracetamol")
