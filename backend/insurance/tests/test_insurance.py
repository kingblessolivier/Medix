"""Eligibility, coverage, claims and capitation.

Phase 5's exit criterion is one sentence: a covered sale splits into
co-pay and claim correctly, and the claim reconciles on payment. That is
`TestTheExitCriterion` at the foot of this file.

The other load-bearing test is
`test_capitation_raises_no_claim`. Fee-for-service and capitation are not
the same workflow with a different rate — under capitation the scheme has
already paid, and claiming as well would be asking twice. That is why
`SchemeContract.model` exists, and it is the mitigation `docs/11` R3
records against the open V3 question.
"""

from datetime import date, timedelta

import pytest

from catalog.models import Category, LegalStatus, TaxTreatment
from core.exceptions import DomainError
from core.models import Branch, LicenceKind, LicenceStatus, PremisesLicence, User
from core.quantity import Quantity
from insurance import services
from insurance.models import (
    CapitationPeriod,
    CapitationReceipt,
    Claim,
    ClaimStatus,
    CoverageRule,
    CoverageScope,
    Member,
    ReimbursementModel,
    Scheme,
    SchemeContract,
)
from inventory import services as inventory
from inventory.models import MovementKind
from sales import payments as payment_services
from sales import services as sales
from sales.models import Patient, TaxRule
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db

TODAY = date.today()
NEXT_YEAR = TODAY + timedelta(days=365)


@pytest.fixture
def pharmacy():
    org = make_org("Kigali Care", kind=LicenceKind.RETAIL_PHARMACY)
    branch, _ = Branch.objects.get_or_create(
        organization=org, code="MAIN", defaults={"name": "Main"}
    )
    PremisesLicence.objects.create(
        organization=org,
        branch=branch,
        kind=LicenceKind.RETAIL_PHARMACY,
        number="RFDA-RET-001",
        issued_on=TODAY - timedelta(days=400),
        expiry=NEXT_YEAR,
        status=LicenceStatus.ACTIVE,
    )
    user = User.objects.create_user(username="marie", password="x", organization=org)
    location = make_location(org, "Store", "MAIN")
    TaxRule.objects.create(
        organization=org,
        treatment=TaxTreatment.STANDARD,
        rate_basis_points=0,
        effective_from=TODAY - timedelta(days=365),
    )
    patient = Patient.objects.create(organization=org, full_name="Aline M.")
    scheme = Scheme.objects.create(organization=org, name="RSSB", code="RSSB")
    return {
        "org": org, "branch": branch, "user": user, "location": location,
        "patient": patient, "scheme": scheme,
    }


def contract(pharmacy, *, model=ReimbursementModel.FEE_FOR_SERVICE, **overrides):
    return SchemeContract.objects.create(
        organization=pharmacy["org"],
        scheme=pharmacy["scheme"],
        model=model,
        effective_from=TODAY - timedelta(days=30),
        **overrides,
    )


def member(pharmacy, **overrides):
    defaults = {
        "organization": pharmacy["org"],
        "patient": pharmacy["patient"],
        "scheme": pharmacy["scheme"],
        "member_number": "RSSB-0001",
        "valid_from": TODAY - timedelta(days=100),
    }
    defaults.update(overrides)
    return Member.objects.create(**defaults)


def rule(contract_row, *, percent, scope=CoverageScope.ALL, **overrides):
    fields = {
        "organization": contract_row.organization,
        "contract": contract_row,
        "scope": scope,
        "coverage_basis_points": percent * 100,
        "effective_from": TODAY - timedelta(days=30),
    }
    fields.update(overrides)
    return CoverageRule.objects.create(**fields)


def stocked(pharmacy, name="Amoxicillin 500mg", *, unit_cost=100, category=None):
    product = make_product(
        pharmacy["org"], name, legal_status=LegalStatus.OTC, tax=TaxTreatment.EXEMPT
    )
    if category is not None:
        product.category = category
        product.save(update_fields=["category"])
    batch = make_batch(pharmacy["org"], product, number=f"{name[:3].upper()}-1",
                       unit_cost_base=unit_cost)
    inventory.post_movement(
        organization=pharmacy["org"],
        location=pharmacy["location"],
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        quantity=Quantity(20, uom(product, "PACK")),
    )
    return product


def sell(pharmacy, product, *, packs=1, price=10_000, pay=True):
    sale = sales.start_sale(
        organization=pharmacy["org"],
        branch=pharmacy["branch"],
        location=pharmacy["location"],
        cashier=pharmacy["user"],
    )
    sale.patient = pharmacy["patient"]
    sale.save(update_fields=["patient"])
    sales.add_line(
        sale=sale,
        product=product,
        quantity=packs,
        uom=uom(product, "PACK"),
        unit_price=price,
    )
    sales.complete_sale(sale=sale, performed_by=pharmacy["user"])
    sale.refresh_from_db()
    if pay:
        payment_services.take_payment(
            sale=sale, method="CASH", amount=sale.total, performed_by=pharmacy["user"]
        )
        sale.refresh_from_db()
    return sale


class TestEligibility:
    def test_a_valid_member_under_a_live_contract_is_covered(self, pharmacy):
        contract(pharmacy)
        member(pharmacy)
        found = services.check_eligibility(
            organization=pharmacy["org"], patient=pharmacy["patient"]
        )
        assert found.covered

    def test_no_membership_says_so(self, pharmacy):
        contract(pharmacy)
        found = services.check_eligibility(
            organization=pharmacy["org"], patient=pharmacy["patient"]
        )
        assert not found.covered
        assert "No membership" in found.reason

    def test_an_expired_card_names_the_scheme(self, pharmacy):
        """Three different conversations, not one 'computer says no'."""
        contract(pharmacy)
        member(pharmacy, valid_to=TODAY - timedelta(days=1))
        found = services.check_eligibility(
            organization=pharmacy["org"], patient=pharmacy["patient"]
        )
        assert not found.covered
        assert "expired" in found.reason

    def test_a_real_member_off_the_panel_is_told_why(self, pharmacy):
        """Selective contracting is a fact about the pharmacy."""
        contract(pharmacy, is_contracted=False)
        member(pharmacy)
        found = services.check_eligibility(
            organization=pharmacy["org"], patient=pharmacy["patient"]
        )
        assert not found.covered
        assert "panel" in found.reason
        assert found.member is not None

    def test_no_contract_at_all_is_not_covered(self, pharmacy):
        member(pharmacy)
        found = services.check_eligibility(
            organization=pharmacy["org"], patient=pharmacy["patient"]
        )
        assert not found.covered

    def test_no_patient_is_not_an_error(self, pharmacy):
        found = services.check_eligibility(
            organization=pharmacy["org"], patient=None
        )
        assert not found.covered


class TestCoverage:
    def test_a_percentage_splits_the_line(self, pharmacy):
        row = contract(pharmacy)
        rule(row, percent=85)
        product = stocked(pharmacy)

        split = services.split_amount(contract=row, product=product, amount=10_000)
        assert split.covered == 8_500
        assert split.patient == 1_500

    def test_the_remainder_goes_to_the_scheme(self, pharmacy):
        """Rounding the patient up would overcharge on most lines."""
        row = contract(pharmacy)
        rule(row, percent=85)
        product = stocked(pharmacy)

        split = services.split_amount(contract=row, product=product, amount=999)
        # 999 × 0.85 = 849.15 → the scheme takes 849, the patient 150.
        assert split.covered == 849
        assert split.patient == 150
        assert split.covered + split.patient == 999

    def test_a_narrower_rule_wins(self, pharmacy):
        """Otherwise one blanket rule makes every specific one unreachable."""
        row = contract(pharmacy)
        category = Category.objects.create(organization=pharmacy["org"], name="Antibiotics")
        product = stocked(pharmacy, category=category)

        rule(row, percent=50, scope=CoverageScope.ALL)
        rule(row, percent=90, scope=CoverageScope.CATEGORY, category=category)
        rule(row, percent=100, scope=CoverageScope.PRODUCT, product=product)

        split = services.split_amount(contract=row, product=product, amount=10_000)
        assert split.covered == 10_000

    def test_an_exclusion_is_not_the_same_as_zero_percent(self, pharmacy):
        row = contract(pharmacy)
        rule(row, percent=0, is_excluded=True)
        product = stocked(pharmacy)

        split = services.split_amount(contract=row, product=product, amount=10_000)
        assert split.covered == 0
        assert "Excluded" in split.note

    def test_a_cap_limits_the_covered_amount(self, pharmacy):
        row = contract(pharmacy)
        rule(row, percent=100, maximum_amount=5_000)
        product = stocked(pharmacy)

        split = services.split_amount(contract=row, product=product, amount=12_000)
        assert split.covered == 5_000
        assert split.patient == 7_000

    def test_a_prescription_only_rule_does_not_cover_a_counter_sale(self, pharmacy):
        row = contract(pharmacy)
        rule(row, percent=90, requires_prescription=True)
        product = stocked(pharmacy)

        split = services.split_amount(
            contract=row, product=product, amount=10_000, has_prescription=False
        )
        assert split.covered == 0
        assert "prescription" in split.note

    def test_no_rule_means_the_patient_pays(self, pharmacy):
        row = contract(pharmacy)
        product = stocked(pharmacy)
        split = services.split_amount(contract=row, product=product, amount=10_000)
        assert split.patient == 10_000

    def test_the_rule_in_force_on_the_day_applies(self, pharmacy):
        """A revised percentage must not restate last quarter's co-pays."""
        row = contract(pharmacy)
        product = stocked(pharmacy)
        rule(
            row, percent=50,
            effective_from=TODAY - timedelta(days=200),
            effective_to=TODAY - timedelta(days=100),
        )
        rule(row, percent=90, effective_from=TODAY - timedelta(days=99))

        then = services.split_amount(
            contract=row, product=product, amount=10_000,
            as_of=TODAY - timedelta(days=150),
        )
        now = services.split_amount(contract=row, product=product, amount=10_000)
        assert then.covered == 5_000
        assert now.covered == 9_000


class TestClaims:
    def test_a_covered_sale_raises_a_claim(self, pharmacy):
        row = contract(pharmacy)
        rule(row, percent=80)
        member(pharmacy)
        product = stocked(pharmacy)

        sale = sell(pharmacy, product, price=10_000)
        claim = Claim.objects.get(sale=sale)
        assert claim.claimed_amount == 8_000
        assert claim.patient_paid == 2_000
        assert claim.status == ClaimStatus.DRAFT

    def test_the_submission_deadline_is_frozen_from_the_contract(self, pharmacy):
        """Renegotiating the window must not make an old claim late."""
        row = contract(pharmacy, claim_window_days=14)
        rule(row, percent=80)
        member(pharmacy)
        sale = sell(pharmacy, stocked(pharmacy))

        claim = Claim.objects.get(sale=sale)
        assert claim.submit_by == TODAY + timedelta(days=14)

    def test_submitting_numbers_it(self, pharmacy):
        row = contract(pharmacy)
        rule(row, percent=80)
        member(pharmacy)
        sale = sell(pharmacy, stocked(pharmacy))

        claim = services.submit_claim(
            claim=Claim.objects.get(sale=sale), performed_by=pharmacy["user"]
        )
        assert claim.number.startswith("CLM-")
        assert claim.status == ClaimStatus.SUBMITTED

    def test_an_uncovered_sale_raises_nothing(self, pharmacy):
        product = stocked(pharmacy)
        sale = sell(pharmacy, product)
        assert not Claim.objects.filter(sale=sale).exists()

    def test_a_second_claim_on_one_sale_is_refused(self, pharmacy):
        row = contract(pharmacy)
        rule(row, percent=80)
        member(pharmacy)
        sale = sell(pharmacy, stocked(pharmacy))

        eligibility = services.check_eligibility(
            organization=pharmacy["org"], patient=pharmacy["patient"]
        )
        with pytest.raises(services.AlreadyClaimed):
            services.raise_claim(
                organization=pharmacy["org"],
                sale=sale,
                eligibility=eligibility,
                performed_by=pharmacy["user"],
            )


class TestResponses:
    def setup_claim(self, pharmacy, *, percent=80, price=10_000):
        row = contract(pharmacy)
        rule(row, percent=percent)
        member(pharmacy)
        sale = sell(pharmacy, stocked(pharmacy), price=price)
        claim = Claim.objects.get(sale=sale)
        return services.submit_claim(claim=claim, performed_by=pharmacy["user"])

    def test_a_silent_response_allows_what_was_claimed(self, pharmacy):
        """A remittance naming only rejections is the common shape."""
        claim = self.setup_claim(pharmacy)
        answered = services.record_response(
            claim=claim, performed_by=pharmacy["user"]
        )
        assert answered.allowed_amount == claim.claimed_amount

    def test_a_rejected_line_names_its_reason(self, pharmacy):
        claim = self.setup_claim(pharmacy)
        line = claim.lines.first()
        answered = services.record_response(
            claim=claim,
            performed_by=pharmacy["user"],
            rejections={str(line.id): "Member number invalid"},
        )
        line.refresh_from_db()
        assert line.is_rejected
        assert line.rejection_reason == "Member number invalid"
        assert answered.status == ClaimStatus.REJECTED

    def test_a_rejected_claim_can_be_resubmitted(self, pharmacy):
        """Most rejections are technical. Terminal would write off real money."""
        claim = self.setup_claim(pharmacy)
        line = claim.lines.first()
        services.record_response(
            claim=claim,
            performed_by=pharmacy["user"],
            rejections={str(line.id): "Typo"},
        )
        again = services.submit_claim(claim=claim, performed_by=pharmacy["user"])
        assert again.status == ClaimStatus.RESUBMITTED
        assert again.number.startswith("CLM-")

    def test_outstanding_follows_what_was_allowed_not_claimed(self, pharmacy):
        """Chasing the claimed figure is chasing money already refused."""
        claim = self.setup_claim(pharmacy)
        line = claim.lines.first()
        services.record_response(
            claim=claim,
            performed_by=pharmacy["user"],
            allowed={str(line.id): 5_000},
        )
        claim.refresh_from_db()
        assert claim.claimed_amount == 8_000
        assert claim.allowed_amount == 5_000
        assert claim.outstanding == 5_000

    def test_a_write_off_needs_a_reason(self, pharmacy):
        claim = self.setup_claim(pharmacy)
        with pytest.raises(DomainError):
            services.write_off_claim(
                claim=claim, performed_by=pharmacy["user"], reason=" "
            )


class TestCapitation:
    def test_capitation_raises_no_claim(self, pharmacy):
        """The scheme has already paid. Claiming would be asking twice."""
        row = contract(
            pharmacy,
            model=ReimbursementModel.CAPITATION,
            capitation_amount=500_000,
            capitation_period=CapitationPeriod.MONTH,
        )
        rule(row, percent=100)
        member(pharmacy)

        sale = sell(pharmacy, stocked(pharmacy))
        assert not Claim.objects.filter(sale=sale).exists()

    def test_the_patient_split_still_applies(self, pharmacy):
        """Capitation changes who is billed, not whether cover exists."""
        row = contract(pharmacy, model=ReimbursementModel.CAPITATION)
        rule(row, percent=100)
        member(pharmacy)
        product = stocked(pharmacy)

        split = services.split_amount(contract=row, product=product, amount=10_000)
        assert split.patient == 0

    def test_utilisation_is_measured_against_cost_not_retail(self, pharmacy):
        """Under capitation the pharmacy spends its own money on stock."""
        row = contract(
            pharmacy, model=ReimbursementModel.CAPITATION, capitation_amount=100_000
        )
        rule(row, percent=100)
        member(pharmacy)
        CapitationReceipt.objects.create(
            organization=pharmacy["org"],
            contract=row,
            period_start=TODAY.replace(day=1),
            period_end=TODAY,
            members_covered=50,
            amount=100_000,
        )
        sell(pharmacy, stocked(pharmacy, unit_cost=50), packs=2, price=20_000)

        found = services.capitation_utilisation(
            organization=pharmacy["org"],
            contract=row,
            start=TODAY.replace(day=1),
            end=TODAY,
        )
        assert found["capitation_received"] == 100_000
        assert found["dispensed_cost"] == 200 * 50
        assert found["margin"] == 100_000 - 10_000
        assert not found["over_utilised"]

    def test_over_utilisation_is_reported(self, pharmacy):
        row = contract(
            pharmacy, model=ReimbursementModel.CAPITATION, capitation_amount=1_000
        )
        rule(row, percent=100)
        member(pharmacy)
        CapitationReceipt.objects.create(
            organization=pharmacy["org"],
            contract=row,
            period_start=TODAY.replace(day=1),
            period_end=TODAY,
            amount=1_000,
        )
        sell(pharmacy, stocked(pharmacy, unit_cost=500), packs=5, price=1_000)

        found = services.capitation_utilisation(
            organization=pharmacy["org"],
            contract=row,
            start=TODAY.replace(day=1),
            end=TODAY,
        )
        assert found["over_utilised"]
        assert found["margin"] < 0


class TestReceivables:
    def test_rejections_are_counted_apart_from_the_ageing(self, pharmacy):
        """They are not late, they are refused. Mixing hides recoverable work."""
        row = contract(pharmacy)
        rule(row, percent=80)
        member(pharmacy)

        sale = sell(pharmacy, stocked(pharmacy, "One"))
        claim = services.submit_claim(
            claim=Claim.objects.get(sale=sale), performed_by=pharmacy["user"]
        )
        services.record_response(
            claim=claim,
            performed_by=pharmacy["user"],
            rejections={str(claim.lines.first().id): "Wrong code"},
        )

        found = services.receivables_by_scheme(organization=pharmacy["org"])
        assert found["rejected_total"] == 8_000
        assert found["total"] == 0

    def test_buckets_sum_to_the_total(self, pharmacy):
        row = contract(pharmacy)
        rule(row, percent=80)
        member(pharmacy)
        sale = sell(pharmacy, stocked(pharmacy))
        claim = services.submit_claim(
            claim=Claim.objects.get(sale=sale), performed_by=pharmacy["user"]
        )
        services.record_response(claim=claim, performed_by=pharmacy["user"])

        found = services.receivables_by_scheme(organization=pharmacy["org"])
        assert sum(found["buckets"].values()) == found["total"]
        assert found["schemes"][0]["scheme"] == "RSSB"


class TestTheExitCriterion:
    """A covered sale splits into co-pay and claim, and reconciles on payment.

    docs/09 Phase 5, in one test.
    """

    def test_split_claim_and_reconcile(self, pharmacy):
        row = contract(pharmacy)
        rule(row, percent=85)
        member(pharmacy)
        product = stocked(pharmacy)

        # Dispense 20,000 of cover at 85%.
        sale = sell(pharmacy, product, packs=2, price=10_000, pay=False)

        eligibility = services.check_eligibility(
            organization=pharmacy["org"], patient=pharmacy["patient"]
        )
        priced = services.price_sale(
            organization=pharmacy["org"], sale=sale, eligibility=eligibility
        )

        # The split adds up to the bill, exactly.
        assert priced["scheme_amount"] == 17_000
        assert priced["patient_amount"] == 3_000
        assert priced["scheme_amount"] + priced["patient_amount"] == priced["gross"]

        # The claim carries the scheme's half.
        claim = Claim.objects.get(sale=sale)
        assert claim.claimed_amount == 17_000
        assert claim.patient_paid == 3_000

        # The patient pays their share and only their share.
        payment_services.take_payment(
            sale=sale, method="CASH", amount=3_000, performed_by=pharmacy["user"]
        )

        # Submitted, allowed in full, then paid in two remittances.
        services.submit_claim(claim=claim, performed_by=pharmacy["user"])
        services.record_response(claim=claim, performed_by=pharmacy["user"])
        claim.refresh_from_db()
        assert claim.outstanding == 17_000

        services.record_claim_payment(
            claim=claim, amount=10_000, performed_by=pharmacy["user"]
        )
        claim.refresh_from_db()
        assert claim.status == ClaimStatus.PART_PAID
        assert claim.outstanding == 7_000

        services.record_claim_payment(
            claim=claim, amount=7_000, performed_by=pharmacy["user"]
        )
        claim.refresh_from_db()
        assert claim.status == ClaimStatus.PAID
        assert claim.outstanding == 0

        # And it reconciles: what the patient paid plus what the scheme
        # paid is the whole bill.
        assert claim.settled + claim.patient_paid == priced["gross"]

    def test_overpayment_is_refused(self, pharmacy):
        row = contract(pharmacy)
        rule(row, percent=85)
        member(pharmacy)
        sale = sell(pharmacy, stocked(pharmacy))
        claim = services.submit_claim(
            claim=Claim.objects.get(sale=sale), performed_by=pharmacy["user"]
        )
        services.record_response(claim=claim, performed_by=pharmacy["user"])
        claim.refresh_from_db()

        with pytest.raises(DomainError):
            services.record_claim_payment(
                claim=claim,
                amount=claim.outstanding + 1,
                performed_by=pharmacy["user"],
            )
