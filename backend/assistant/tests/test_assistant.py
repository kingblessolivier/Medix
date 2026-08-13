"""What the Assistant may and may not do.

The class that carries the weight is `TestItNeverActs`. Everything else
here is about answering well; that one is about the rule in CLAUDE.md —
*the Assistant never silently performs an action that moves stock, money,
or a regulated record* — and it is written as an assertion about the code
rather than about one code path, because a rule that only holds for the
handlers written so far is not a rule.
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from assistant import intents, services
from assistant.models import Proposal, ProposalStatus
from commerce.models import (
    Availability,
    PurchaseOrder,
    TradingRelationship,
    VendorListing,
)
from core.exceptions import DomainError
from core.models import Branch, User
from core.quantity import Quantity
from inventory import services as inventory
from inventory.models import MovementKind
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db


@pytest.fixture
def pharmacy():
    org = make_org("Kigali Care")
    Branch.objects.create(organization=org, name="Main", code="MAIN")
    user = User.objects.create_user(username="marie", password="x", organization=org)
    location = make_location(org, "Main store", "MAIN")

    product = make_product(org, "Amoxicillin 500mg")
    product.reorder_point_base = 5_000
    product.save(update_fields=["reorder_point_base"])
    batch = make_batch(org, product, number="AMX-1", expires_in_days=40)
    inventory.post_movement(
        organization=org,
        location=location,
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        quantity=Quantity(10, uom(product, "PACK")),
    )

    client = APIClient()
    client.force_authenticate(user=user)
    return {
        "org": org, "user": user, "location": location,
        "product": product, "batch": batch, "client": client,
    }


def ask(pharmacy, question):
    return services.ask(user=pharmacy["user"], question=question).answer


class TestClinicalRefusal:
    """No symptom-to-drug mapping. Not at any confidence, not ever."""

    @pytest.mark.parametrize(
        "question",
        [
            "what should I give for a headache",
            "best for fever in a child",
            "treatment for malaria",
            "can a patient take this in pregnancy",
            "what is the dose for amoxicillin",
            "is it safe to take with alcohol",
        ],
    )
    def test_a_clinical_question_is_refused(self, pharmacy, question):
        answer = ask(pharmacy, question)
        assert answer.intent == "clinical"
        assert answer.rows == []

    def test_the_refusal_says_where_information_lives(self, pharmacy):
        answer = ask(pharmacy, "what should I give for a headache")
        assert "leaflet" in answer.note

    def test_it_beats_a_product_search_to_the_question(self, pharmacy):
        """The dangerous failure is falling through to something plausible."""
        make_product(pharmacy["org"], "Paracetamol 500mg")
        answer = ask(pharmacy, "what should I give for a headache, paracetamol?")
        assert answer.intent == "clinical"


class TestProfitIsNotNetProfit:
    def test_asking_about_profit_answers_operating_result(self, pharmacy):
        answer = ask(pharmacy, "what is my profit this month")
        assert answer.intent == "operating_result"

    def test_the_words_net_profit_never_appear(self, pharmacy):
        answer = ask(pharmacy, "show me net profit")
        text = " ".join(
            [answer.headline, answer.note, *[str(row) for row in answer.rows]]
        ).lower()
        assert "net profit" not in text.replace("not net profit", "")

    def test_it_says_what_is_missing_from_the_figure(self, pharmacy):
        answer = ask(pharmacy, "how did we do last month")
        assert "tax" in answer.note and "rent" in answer.note


class TestQuestions:
    def test_expiring_batches(self, pharmacy):
        answer = ask(pharmacy, "which batches expire in 60 days")
        assert answer.intent == "expiring"
        assert len(answer.rows) == 1

    def test_a_shorter_horizon_narrows_the_answer(self, pharmacy):
        """The batch has forty days left; a thirty-day question excludes it."""
        assert ask(pharmacy, "expiring in 30 days").rows == []

    def test_below_reorder_point(self, pharmacy):
        answer = ask(pharmacy, "what is running low")
        assert answer.intent == "low_stock"
        assert answer.rows[0]["product"] == "Amoxicillin 500mg"

    def test_stock_of_a_named_product(self, pharmacy):
        answer = ask(pharmacy, "how much amoxicillin do we have")
        assert answer.intent == "stock_of"
        assert "1,000 available" in answer.headline

    def test_a_product_nobody_stocks(self, pharmacy):
        answer = ask(pharmacy, "how much insulin do we have")
        assert "No product matching" in answer.headline

    def test_unpaid_invoices(self, pharmacy):
        answer = ask(pharmacy, "show unpaid supplier invoices")
        assert answer.intent == "unpaid_invoices"

    def test_cold_chain(self, pharmacy):
        answer = ask(pharmacy, "any fridge problems")
        assert answer.intent == "cold_chain"

    def test_an_unmatched_question_falls_back_to_search(self, pharmacy):
        answer = ask(pharmacy, "AMX-1")
        assert answer.intent == "search"
        assert answer.rows

    def test_an_empty_question_asks_for_one(self, pharmacy):
        assert ask(pharmacy, "  ").intent == "empty"

    def test_every_answer_names_a_screen(self, pharmacy):
        for question in [
            "what expires soon",
            "what is running low",
            "unpaid invoices",
            "best sellers",
            "what is not selling",
            "fridge problems",
            "how much amoxicillin do we have",
        ]:
            assert ask(pharmacy, question).screen, question


class TestItNeverActs:
    """The rule, checked as a property rather than case by case."""

    def test_asking_writes_nothing_but_a_proposal(self, pharmacy):
        from inventory.models import StockMovement

        before = StockMovement.objects.count()
        for question in [
            "what is running low",
            "what expires soon",
            "quarantine batch AMX-1",
            "order more amoxicillin",
            "create a purchase order",
        ]:
            ask(pharmacy, question)
        assert StockMovement.objects.count() == before
        assert not PurchaseOrder.objects.exists()

    def test_a_suggestion_comes_back_as_a_proposal(self, pharmacy):
        answer = ask(pharmacy, "what is running low")
        assert answer.proposal["action"] == "draft_order"
        assert Proposal.objects.get().status == ProposalStatus.PROPOSED

    def test_the_proposal_states_its_effect(self, pharmacy):
        answer = ask(pharmacy, "what is running low")
        assert "Creates a draft order" in answer.proposal["effect"]

    def test_the_client_cannot_name_its_own_action(self, pharmacy):
        """Confirming refers to a stored row, never to posted arguments."""
        import inspect

        signature = inspect.signature(services.confirm)
        assert set(signature.parameters) == {"user", "proposal", "accepted", "reason"}

    def test_every_named_action_exists(self):
        """A handler suggesting an action nobody implemented is a bug."""
        for intent in intents.INTENTS:
            source = inspect_source(intent.handler)
            for name in ("draft_order", "quarantine_batch"):
                if f'"{name}"' in source:
                    assert name in services.ACTIONS


def inspect_source(function) -> str:
    import inspect

    return inspect.getsource(function)


class TestConfirmation:
    @pytest.fixture
    def supplier(self, pharmacy):
        """A depot that lists the product, matched by registration number."""
        from catalog.models import ProductRegistration

        depot = make_org("ABC Wholesale", kind="WHOLESALE")
        TradingRelationship.objects.create(
            organization=depot, customer=pharmacy["org"], is_active=True
        )

        theirs = make_product(depot, "Amoxicillin 500mg")
        for product in (pharmacy["product"], theirs):
            ProductRegistration.objects.create(
                organization=product.organization,
                product=product,
                registration_number="RW-AMX-500",
            )
        VendorListing.objects.create(
            organization=depot,
            product=theirs,
            price=12_000_00,
            price_uom=uom(theirs, "PACK"),
            availability=Availability.AVAILABLE_NOW,
            offered_base=100_000,
        )
        return depot

    def proposal(self, pharmacy):
        services.ask(user=pharmacy["user"], question="what is running low")
        return Proposal.objects.get()

    def test_confirming_creates_the_draft(self, pharmacy, supplier):
        proposal = self.proposal(pharmacy)
        services.confirm(user=pharmacy["user"], proposal=proposal, accepted=True)

        proposal.refresh_from_db()
        assert proposal.status == ProposalStatus.CONFIRMED
        assert PurchaseOrder.objects.count() == 1

    def test_the_draft_is_only_a_draft(self, pharmacy, supplier):
        """It still goes through both approvals. The typing is saved, not the judgement."""
        from commerce.models import PurchaseOrderStatus

        services.confirm(
            user=pharmacy["user"], proposal=self.proposal(pharmacy), accepted=True
        )
        assert PurchaseOrder.objects.get().status == PurchaseOrderStatus.DRAFT

    def test_declining_creates_nothing(self, pharmacy, supplier):
        proposal = self.proposal(pharmacy)
        services.confirm(user=pharmacy["user"], proposal=proposal, accepted=False)

        proposal.refresh_from_db()
        assert proposal.status == ProposalStatus.DECLINED
        assert not PurchaseOrder.objects.exists()

    def test_a_decline_is_kept(self, pharmacy, supplier):
        """What was suggested and what was thought of it is the useful pair."""
        proposal = self.proposal(pharmacy)
        services.confirm(
            user=pharmacy["user"],
            proposal=proposal,
            accepted=False,
            reason="Ordering from the other depot this week.",
        )
        proposal.refresh_from_db()
        assert proposal.reason.startswith("Ordering")

    def test_deciding_twice_is_refused(self, pharmacy, supplier):
        proposal = self.proposal(pharmacy)
        services.confirm(user=pharmacy["user"], proposal=proposal, accepted=True)
        with pytest.raises(DomainError) as raised:
            services.confirm(user=pharmacy["user"], proposal=proposal, accepted=True)
        assert raised.value.code == "already_decided"

    def test_a_stale_proposal_lapses(self, pharmacy, supplier):
        """It is a snapshot. Confirming an old one acts on figures that moved."""
        proposal = self.proposal(pharmacy)
        proposal.expires_at = timezone.now() - timedelta(minutes=1)
        proposal.save(update_fields=["expires_at"])

        with pytest.raises(DomainError) as raised:
            services.confirm(user=pharmacy["user"], proposal=proposal, accepted=True)
        assert raised.value.code == "proposal_expired"
        proposal.refresh_from_db()
        assert proposal.status == ProposalStatus.EXPIRED

    def test_a_failing_action_is_recorded_not_lost(self, pharmacy):
        """No supplier is set up, so the draft cannot be raised."""
        proposal = self.proposal(pharmacy)
        services.confirm(user=pharmacy["user"], proposal=proposal, accepted=True)

        proposal.refresh_from_db()
        assert proposal.status == ProposalStatus.FAILED
        assert "no supplier" in proposal.error.lower()

    def test_a_failure_leaves_no_half_built_order(self, pharmacy):
        services.confirm(
            user=pharmacy["user"], proposal=self.proposal(pharmacy), accepted=True
        )
        assert not PurchaseOrder.objects.exists()


class TestAudit:
    def test_asking_is_recorded(self, pharmacy):
        from core.models import AuditEvent

        ask(pharmacy, "what expires soon")
        assert AuditEvent.objects.filter(action="assistant.asked").exists()

    def test_confirming_is_recorded_under_its_outcome(self, pharmacy):
        from core.models import AuditEvent

        services.ask(user=pharmacy["user"], question="what is running low")
        services.confirm(
            user=pharmacy["user"], proposal=Proposal.objects.get(), accepted=True
        )
        assert AuditEvent.objects.filter(action="assistant.failed").exists()


class TestApi:
    def test_ask_answers(self, pharmacy):
        response = pharmacy["client"].post(
            "/api/v1/assistant/ask/", {"question": "what expires soon"}, format="json"
        )
        assert response.status_code == 200
        assert response.data["intent"] == "expiring"

    def test_a_proposal_is_returned_with_an_id(self, pharmacy):
        response = pharmacy["client"].post(
            "/api/v1/assistant/ask/", {"question": "what is running low"}, format="json"
        )
        assert response.data["proposal"]["id"]

    def test_deciding_without_accepted_declines(self, pharmacy):
        """A confirmation that defaults to yes is not a confirmation."""
        pharmacy["client"].post(
            "/api/v1/assistant/ask/", {"question": "what is running low"}, format="json"
        )
        proposal = Proposal.objects.get()
        response = pharmacy["client"].post(
            f"/api/v1/assistant/proposals/{proposal.id}/decide/", {}, format="json"
        )
        assert response.data["status"] == ProposalStatus.DECLINED

    def test_another_tenant_cannot_decide(self, pharmacy):
        other = make_org("ABC Wholesale", kind="WHOLESALE")
        stranger = User.objects.create_user(
            username="jean", password="x", organization=other
        )
        client = APIClient()
        client.force_authenticate(user=stranger)

        services.ask(user=pharmacy["user"], question="what is running low")
        proposal = Proposal.objects.get()

        response = client.post(
            f"/api/v1/assistant/proposals/{proposal.id}/decide/",
            {"accepted": True},
            format="json",
        )
        assert response.status_code == 404

    def test_proposals_are_listed(self, pharmacy):
        services.ask(user=pharmacy["user"], question="what is running low")
        response = pharmacy["client"].get("/api/v1/proposals/")
        assert response.data["count"] == 1

    def test_anonymous_cannot_ask(self):
        response = APIClient().post(
            "/api/v1/assistant/ask/", {"question": "profit"}, format="json"
        )
        assert response.status_code in (401, 403)

    def test_the_question_box_is_rate_limited(self, pharmacy):
        """30/min per user — docs/07. A looping client is the shape here."""
        from django.core.cache import cache

        cache.clear()
        codes = {
            pharmacy["client"]
            .post("/api/v1/assistant/ask/", {"question": "profit"}, format="json")
            .status_code
            for _ in range(32)
        }
        assert 429 in codes
