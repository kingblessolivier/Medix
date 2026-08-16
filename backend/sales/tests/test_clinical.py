"""Clinical checks, and the interaction check that deliberately is not one.

The test that matters most is
`test_no_dataset_reports_not_checked_not_clear`. A pharmacist who sees
no interaction warning must be told nobody looked, because otherwise
they reasonably conclude the pair was checked and found safe — and every
interaction we failed to encode becomes a silent assurance nobody earned.
"""

from datetime import date, timedelta

import pytest

from catalog.models import ClinicalAttribute, ClinicalAttributeKind
from core.alerts import Severity
from sales import clinical, interactions
from sales.models import Patient, PatientAllergy
from inventory.tests.factories import make_org, make_product

pytestmark = pytest.mark.django_db

TODAY = date.today()


@pytest.fixture
def org():
    return make_org("Kigali Care")


@pytest.fixture
def patient(org):
    return Patient.objects.create(
        organization=org,
        full_name="Aline M.",
        date_of_birth=TODAY - timedelta(days=365 * 30),
    )


def attribute(org, product, kind, *, number=None, text="", source="SmPC", **dates):
    return ClinicalAttribute.objects.create(
        organization=org,
        product=product,
        kind=kind,
        value_number=number,
        value_text=text,
        source=source,
        **dates,
    )


class TestAllergy:
    def test_a_recorded_allergen_matches_the_ingredient(self, org, patient):
        product = make_product(org, "Amoxil 500mg")
        attribute(
            org, product, ClinicalAttributeKind.ACTIVE_INGREDIENT, text="amoxicillin"
        )
        PatientAllergy.objects.create(
            organization=org, patient=patient, allergen="Amoxicillin", severity="SEVERE"
        )

        found = clinical.allergy_contraindication(patient=patient, product=product)
        assert [alert.code for alert in found] == ["ALLERGY_CONTRAINDICATION"]
        assert found[0].severity == Severity.WARNING

    def test_it_matches_under_a_different_brand(self, org, patient):
        """Allergy is to the ingredient, not the trade name."""
        product = make_product(org, "Ospamox 500mg")
        attribute(
            org, product, ClinicalAttributeKind.ACTIVE_INGREDIENT, text="Amoxicillin"
        )
        PatientAllergy.objects.create(
            organization=org, patient=patient, allergen="amoxicillin  "
        )
        assert clinical.allergy_contraindication(patient=patient, product=product)

    def test_an_unrelated_allergy_is_silent(self, org, patient):
        product = make_product(org, "Paracetamol 500mg")
        attribute(
            org, product, ClinicalAttributeKind.ACTIVE_INGREDIENT, text="paracetamol"
        )
        PatientAllergy.objects.create(
            organization=org, patient=patient, allergen="Penicillin"
        )
        assert clinical.allergy_contraindication(patient=patient, product=product) == []

    def test_no_patient_means_no_check(self, org):
        product = make_product(org, "Amoxil")
        assert clinical.allergy_contraindication(patient=None, product=product) == []

    def test_the_generic_name_is_the_fallback(self, org, patient):
        """Better than not matching when no ingredient is recorded."""
        product = make_product(org, "Amoxicillin 500mg")
        PatientAllergy.objects.create(
            organization=org, patient=patient, allergen="amoxicillin"
        )
        assert clinical.allergy_contraindication(patient=patient, product=product)


class TestDuplicateTherapy:
    def test_two_in_one_category_warn(self, org):
        from catalog.models import Category

        category = Category.objects.create(organization=org, name="Antibiotics")
        one = make_product(org, "Amoxicillin 500mg")
        two = make_product(org, "Ampicillin 500mg")
        for product in (one, two):
            product.category = category
            product.save(update_fields=["category"])

        found = clinical.duplicate_therapy(products=[one, two])
        assert [alert.code for alert in found] == ["DUPLICATE_THERAPY"]

    def test_one_per_category_is_silent(self, org):
        from catalog.models import Category

        category = Category.objects.create(organization=org, name="Antibiotics")
        product = make_product(org, "Amoxicillin 500mg")
        product.category = category
        product.save(update_fields=["category"])
        assert clinical.duplicate_therapy(products=[product]) == []

    def test_uncategorised_products_are_skipped(self, org):
        """Otherwise every uncategorised pair matches and it becomes noise."""
        one = make_product(org, "Thing one")
        two = make_product(org, "Thing two")
        assert clinical.duplicate_therapy(products=[one, two]) == []


class TestDemographicRestriction:
    def test_under_the_recorded_minimum_warns(self, org, patient):
        product = make_product(org, "Aspirin 300mg")
        attribute(org, product, ClinicalAttributeKind.MIN_AGE_YEARS, number=16)
        patient.date_of_birth = TODAY - timedelta(days=365 * 10)
        patient.save(update_fields=["date_of_birth"])

        found = clinical.demographic_restriction(patient=patient, product=product)
        assert [alert.code for alert in found] == ["DEMOGRAPHIC_RESTRICTION"]
        assert "Source" in found[0].detail

    def test_at_the_minimum_exactly_is_silent(self, org, patient):
        product = make_product(org, "Aspirin 300mg")
        attribute(org, product, ClinicalAttributeKind.MIN_AGE_YEARS, number=16)
        patient.date_of_birth = TODAY - timedelta(days=365 * 16 + 4)
        patient.save(update_fields=["date_of_birth"])
        assert clinical.demographic_restriction(patient=patient, product=product) == []

    def test_a_restriction_with_no_birth_date_says_it_could_not_check(self, org, patient):
        """Skipped must not look like passed."""
        product = make_product(org, "Aspirin 300mg")
        attribute(org, product, ClinicalAttributeKind.MIN_AGE_YEARS, number=16)
        patient.date_of_birth = None
        patient.save(update_fields=["date_of_birth"])

        found = clinical.demographic_restriction(patient=patient, product=product)
        assert [alert.code for alert in found] == ["DEMOGRAPHIC_UNCHECKED"]

    def test_no_recorded_restriction_produces_nothing(self, org, patient):
        """Silence means 'nothing on file', never 'safe'."""
        product = make_product(org, "Paracetamol 500mg")
        assert clinical.demographic_restriction(patient=patient, product=product) == []

    def test_pregnancy_restriction_fires_only_when_recorded_pregnant(self, org, patient):
        product = make_product(org, "Isotretinoin 20mg")
        attribute(org, product, ClinicalAttributeKind.PREGNANCY_RESTRICTED, number=1)

        assert clinical.demographic_restriction(patient=patient, product=product) == []
        patient.is_pregnant = True
        patient.save(update_fields=["is_pregnant"])
        found = clinical.demographic_restriction(patient=patient, product=product)
        assert [alert.code for alert in found] == ["PREGNANCY_RESTRICTION"]


class TestMaximumDailyDose:
    def test_over_the_recorded_maximum_warns(self, org):
        product = make_product(org, "Paracetamol 500mg")
        attribute(org, product, ClinicalAttributeKind.MAX_DAILY_DOSE_BASE, number=8)

        found = clinical.maximum_daily_dose(
            product=product, quantity_base=100, days=7
        )
        assert [alert.code for alert in found] == ["MAXIMUM_DAILY_DOSE"]
        assert found[0].meta["per_day"] == 14

    def test_at_the_maximum_is_silent(self, org):
        product = make_product(org, "Paracetamol 500mg")
        attribute(org, product, ClinicalAttributeKind.MAX_DAILY_DOSE_BASE, number=8)
        assert clinical.maximum_daily_dose(product=product, quantity_base=56, days=7) == []

    def test_an_unknown_duration_does_not_guess(self, org):
        """Dividing by a guess would be calculating a dose. It does not."""
        product = make_product(org, "Paracetamol 500mg")
        attribute(org, product, ClinicalAttributeKind.MAX_DAILY_DOSE_BASE, number=8)
        assert clinical.maximum_daily_dose(product=product, quantity_base=999, days=0) == []

    def test_no_recorded_maximum_produces_nothing(self, org):
        product = make_product(org, "Paracetamol 500mg")
        assert clinical.maximum_daily_dose(product=product, quantity_base=999, days=1) == []


class TestEffectiveDating:
    def test_the_value_in_force_on_the_day_applies(self, org, patient):
        """A dose revised down last month must not indict last year."""
        product = make_product(org, "Codeine 30mg")
        attribute(
            org,
            product,
            ClinicalAttributeKind.MAX_DAILY_DOSE_BASE,
            number=12,
            effective_from=TODAY - timedelta(days=400),
            effective_to=TODAY - timedelta(days=200),
        )
        attribute(
            org,
            product,
            ClinicalAttributeKind.MAX_DAILY_DOSE_BASE,
            number=6,
            effective_from=TODAY - timedelta(days=199),
        )

        # 10 a day: inside the old limit, over the current one.
        then = clinical.maximum_daily_dose(
            product=product,
            quantity_base=70,
            days=7,
            as_of=TODAY - timedelta(days=300),
        )
        now = clinical.maximum_daily_dose(product=product, quantity_base=70, days=7)
        assert then == []
        assert [alert.code for alert in now] == ["MAXIMUM_DAILY_DOSE"]

    def test_a_source_is_required(self, org):
        """A threshold with no cited origin is an opinion."""
        from django.db.utils import IntegrityError

        product = make_product(org, "Codeine 30mg")
        with pytest.raises(IntegrityError):
            ClinicalAttribute.objects.create(
                organization=org,
                product=product,
                kind=ClinicalAttributeKind.MAX_DAILY_DOSE_BASE,
                value_number=6,
                source="",
            )


class TestInteractionsAreNotHandBuilt:
    """docs/29 §3.2. The absence is the feature."""

    def test_no_dataset_reports_not_checked_not_clear(self, org):
        """The single most important assertion in this file.

        A pharmacist shown nothing concludes the pair was checked. So
        with no dataset the state is NOT_AVAILABLE, never CLEAR.
        """
        one = make_product(org, "Warfarin 5mg")
        two = make_product(org, "Aspirin 300mg")

        result = interactions.check(products=[one, two])
        assert result.state == interactions.CheckState.NOT_AVAILABLE
        assert result.alerts == []
        assert not result.was_checked

    def test_the_notice_says_so_plainly(self):
        assert "not checked" in interactions.NOT_CHECKED_NOTICE.lower()
        assert "licensed" in interactions.NOT_CHECKED_NOTICE.lower()

    def test_the_default_provider_refuses_rather_than_returning_empty(self):
        with pytest.raises(NotImplementedError):
            interactions.NoDatasetProvider().interactions(["warfarin", "aspirin"])

    def test_a_licensed_provider_plugs_in(self, org, settings):
        """Everything but the clinical content is built."""
        settings.CLINICAL_INTERACTION_PROVIDER = (
            "sales.tests.test_clinical.FakeLicensedProvider"
        )
        one = make_product(org, "Warfarin 5mg")
        two = make_product(org, "Aspirin 300mg")
        for product, ingredient in ((one, "warfarin"), (two, "aspirin")):
            attribute(
                org, product, ClinicalAttributeKind.ACTIVE_INGREDIENT, text=ingredient
            )

        result = interactions.check(products=[one, two])
        assert result.state == interactions.CheckState.FOUND
        assert [alert.code for alert in result.alerts] == ["DRUG_INTERACTION"]
        assert result.provider == "fake"
        assert result.dataset_version == "2026.08"

    def test_a_licensed_provider_reporting_nothing_is_clear_not_unavailable(
        self, org, settings
    ):
        settings.CLINICAL_INTERACTION_PROVIDER = (
            "sales.tests.test_clinical.EmptyLicensedProvider"
        )
        one = make_product(org, "Paracetamol 500mg")
        two = make_product(org, "Vitamin C")
        for product, ingredient in ((one, "paracetamol"), (two, "ascorbic acid")):
            attribute(
                org, product, ClinicalAttributeKind.ACTIVE_INGREDIENT, text=ingredient
            )

        result = interactions.check(products=[one, two])
        assert result.state == interactions.CheckState.CLEAR
        assert result.was_checked

    def test_even_a_contraindicated_grading_is_a_warning(self, org, settings):
        """A hard stop gets worked around; an acknowledgement gets recorded."""
        settings.CLINICAL_INTERACTION_PROVIDER = (
            "sales.tests.test_clinical.FakeLicensedProvider"
        )
        one = make_product(org, "Warfarin 5mg")
        two = make_product(org, "Aspirin 300mg")
        for product, ingredient in ((one, "warfarin"), (two, "aspirin")):
            attribute(
                org, product, ClinicalAttributeKind.ACTIVE_INGREDIENT, text=ingredient
            )

        result = interactions.check(products=[one, two])
        assert result.alerts[0].severity == Severity.WARNING


class FakeLicensedProvider:
    """Stands in for a licensed dataset. Test-only, and it says so."""

    name = "fake"
    dataset_version = "2026.08"

    def interactions(self, ingredients):
        if {"warfarin", "aspirin"} <= set(ingredients):
            return [
                {
                    "a": "warfarin",
                    "b": "aspirin",
                    "severity": "CONTRAINDICATED",
                    "summary": "Increased bleeding risk.",
                    "reference": "FAKE-001",
                }
            ]
        return []


class EmptyLicensedProvider(FakeLicensedProvider):
    name = "empty"

    def interactions(self, ingredients):
        return []
