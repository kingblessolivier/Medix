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
