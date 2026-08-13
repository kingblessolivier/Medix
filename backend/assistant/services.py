"""Ask, and confirm.

Two entry points, and the split between them is the whole design.
`ask()` reads. It can suggest, and a suggestion is a row in the database
with an expiry on it, but `ask()` has no path to a service that writes.
`confirm()` writes, and only from a proposal a person just looked at.

That is CLAUDE.md's rule — *the Assistant never silently performs an
action that moves stock, money, or a regulated record* — expressed as
structure rather than as discipline. A future intent handler cannot
accidentally acquire the power to post a movement, because the function
that runs handlers never had it.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from core import audit
from core.exceptions import DomainError
from core.models import Organization, User
from assistant import intents
from assistant.models import Proposal, ProposalStatus


@dataclass(frozen=True)
class Asked:
    answer: intents.Answer
    proposal: Proposal | None


def ask(*, user: User, question: str) -> Asked:
    """Answer a question. Never acts on it.

    A proposal, where one is offered, is stored so that confirming it
    later refers to something the system wrote rather than to arguments
    the client made up. A client that could name its own action and
    arguments at confirm time would have bypassed the whole gate.
    """
    question = (question or "").strip()
    organization = user.organization
    if organization is None:
        raise DomainError("No active organization.", code="no_organization")
    if len(question) < 2:
        return Asked(
            intents.Answer(intent="empty", headline="Ask a question", screen="overview"),
            None,
        )

    intent = intents.match(question)
    answer = intent.handler(organization=organization, question=question, user=user)

    proposal = None
    if answer.proposal:
        if answer.proposal["action"] not in ACTIONS:
            # A handler naming an action that does not exist is a bug, and
            # a bug here would be a suggestion nobody can honour.
            raise DomainError(
                f"Unknown action '{answer.proposal['action']}'.", code="unknown_action"
            )
        proposal = Proposal.objects.create(
            organization=organization,
            question=question,
            action=answer.proposal["action"],
            arguments=answer.proposal.get("arguments", {}),
            effect=answer.proposal["effect"],
            created_by=user,
        )
        answer = intents.Answer(
            **{
                **answer.__dict__,
                "proposal": {
                    "id": str(proposal.id),
                    "action": proposal.action,
                    "effect": proposal.effect,
                    "expires_at": proposal.expires_at.isoformat(),
                },
            }
        )

    audit.record(
        action="assistant.asked",
        subject=proposal,
        actor=user,
        after={"question": question, "intent": answer.intent, "rows": len(answer.rows)},
        organization=organization,
    )
    return Asked(answer, proposal)


def confirm(*, user: User, proposal: Proposal, accepted: bool, reason: str = "") -> Proposal:
    """Carry out what was proposed, or record that it was declined.

    Declining is recorded rather than discarded: what the system
    suggested and what the pharmacist thought of it is the more useful
    half of the pair.

    Not wrapped in one transaction: lapsing a stale proposal is a write
    that must survive the exception it raises, and an outer atomic block
    would roll it back and leave the row looking live.
    """
    if proposal.status != ProposalStatus.PROPOSED:
        raise DomainError("This was already decided.", code="already_decided")
    if timezone.now() >= proposal.expires_at:
        proposal.status = ProposalStatus.EXPIRED
        proposal.decided_at = timezone.now()
        proposal.save(update_fields=["status", "decided_at", "modified_at"])
        raise DomainError(
            "This proposal has lapsed. Ask again.", code="proposal_expired"
        )

    proposal.decided_at = timezone.now()
    proposal.modified_by = user
    proposal.reason = reason

    if not accepted:
        proposal.status = ProposalStatus.DECLINED
        proposal.save()
        audit.record(
            action="assistant.declined",
            subject=proposal,
            actor=user,
            after={"action": proposal.action, "reason": reason},
            organization=proposal.organization,
        )
        return proposal

    handler = ACTIONS[proposal.action]
    try:
        with transaction.atomic():
            proposal.result = handler(
                organization=proposal.organization,
                arguments=proposal.arguments,
                performed_by=user,
            )
        proposal.status = ProposalStatus.CONFIRMED
    except Exception as exc:
        proposal.status = ProposalStatus.FAILED
        proposal.error = f"{type(exc).__name__}: {exc}"[:2000]

    proposal.save()
    audit.record(
        action=f"assistant.{proposal.status.lower()}",
        subject=proposal,
        actor=user,
        after={
            "action": proposal.action,
            "effect": proposal.effect,
            "result": proposal.result,
            "error": proposal.error,
        },
        organization=proposal.organization,
    )
    return proposal


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------
#
# A whitelist, for the same reason `core.sync` keeps one: this is the
# only place in the system where a stored string chooses what runs, and
# it must not be able to reach an arbitrary service function.


def _draft_order(*, organization: Organization, arguments: dict, performed_by: User) -> dict:
    """A draft purchase order for products below their reorder point.

    Draft, and only draft. It still goes through the same two approvals
    as one typed by hand — the Assistant saves the typing, not the
    judgement.
    """
    from catalog.models import Product
    from commerce import services as commerce
    from commerce.models import TradingRelationship, VendorListing
    from commerce.payloads import _identity_keys
    from inventory.models import Location

    products = list(
        Product.objects.filter(organization=organization, id__in=arguments.get("products", []))
    )
    if not products:
        raise DomainError("Nothing to order.", code="nothing_to_order")

    relationship = (
        TradingRelationship.objects.filter(customer=organization, is_active=True)
        .select_related("organization")
        .first()
    )
    if relationship is None:
        raise DomainError("No supplier is set up for this pharmacy.", code="no_supplier")

    location = Location.objects.filter(organization=organization).order_by("code").first()
    if location is None:
        raise DomainError("No location to deliver to.", code="no_location")

    supplier = relationship.organization
    # The two sides hold different product rows for the same medicine, so
    # the match is on the registration number and GTIN — never on an id.
    listings: dict[str, VendorListing] = {}
    for listing in VendorListing.objects.filter(
        organization=supplier, is_active=True
    ).select_related("product__registration", "price_uom"):
        for key in _identity_keys(listing.product):
            listings.setdefault(key, listing)

    order = commerce.start_order(
        organization=organization,
        supplier=supplier,
        deliver_to=location,
        performed_by=performed_by,
    )

    added = 0
    skipped = 0
    for product in products:
        listing = next(
            (listings[key] for key in _identity_keys(product) if key in listings), None
        )
        if listing is None:
            # A product this depot does not list is left out rather than
            # failing the draft — the rest of the order is still useful.
            skipped += 1
            continue

        shortfall = product.reorder_point_base - _on_hand(
            organization=organization, product=product
        )
        per_unit = listing.price_uom.factor_to_base or 1
        try:
            commerce.add_order_line(
                order=order,
                listing=listing,
                quantity=max(1, -(-shortfall // per_unit)),
                uom=listing.price_uom,
            )
            added += 1
        except DomainError:
            skipped += 1

    return {
        "order": str(order.id),
        "number": order.number,
        "lines": added,
        "skipped": skipped,
    }


def _on_hand(*, organization: Organization, product) -> int:
    from inventory import services as inventory

    return inventory.balance_for(organization=organization, product=product)


def _quarantine_batch(
    *, organization: Organization, arguments: dict, performed_by: User
) -> dict:
    """Hold a batch. Moves stock, so it only ever runs from a confirm."""
    from core.quantity import from_base
    from inventory import movements, services as inventory
    from inventory.models import Batch, Location

    batch = Batch.objects.get(organization=organization, pk=arguments["batch"])
    location = Location.objects.get(organization=organization, pk=arguments["location"])
    held = inventory.ledger_balance_for(
        organization=organization, batch=batch, location=location, status="AVAILABLE"
    )
    if held <= 0:
        raise DomainError("Nothing available to hold.", code="nothing_to_hold")

    movements.quarantine(
        organization=organization,
        batch=batch,
        location=location,
        quantity=from_base(held, batch.product.base_uom),
        performed_by=performed_by,
        reason=arguments.get("reason", "Held from the Assistant."),
    )
    return {"batch": str(batch.id), "quarantined_base": held}


ACTIONS = {
    "draft_order": _draft_order,
    "quarantine_batch": _quarantine_batch,
}
