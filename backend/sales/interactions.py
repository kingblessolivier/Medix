"""Drug–drug interaction checking.

**This module ships with no interaction data, deliberately.**

Interaction checking is the one item in `docs/29` §3 that is genuinely
clinical decision support rather than a data match. Doing it properly
requires a maintained, licensed clinical database — First Databank,
Lexicomp, Medi-Span or a national equivalent — with severity grading and
continuous revision.

A hand-authored table is not a smaller version of that. It is worse than
nothing, because a pharmacist who sees no warning reasonably concludes
the pair was checked and found safe. Every interaction we failed to
encode would become a silent assurance nobody earned.

So what is built here is everything *except* the clinical content: the
provider interface a licensed dataset plugs into, and — until one is
configured — a provider that reports **"not checked"** rather than "no
interactions found". The interface prints that state plainly, which is
the honest half of the decision `docs/30` still records as open.

Configure with `CLINICAL_INTERACTION_PROVIDER`, a dotted path to a
callable returning an `InteractionProvider`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from django.conf import settings
from django.utils.module_loading import import_string

from core.alerts import Alert, Severity, about


class CheckState:
    """Whether the check ran, as three distinct facts.

    `NOT_AVAILABLE` and `CLEAR` are different answers and must never be
    rendered the same way. One says nobody looked; the other says
    somebody looked and found nothing.
    """

    CLEAR = "CLEAR"
    FOUND = "FOUND"
    NOT_AVAILABLE = "NOT_AVAILABLE"


@dataclass(frozen=True)
class InteractionResult:
    state: str
    alerts: list[Alert]
    #: Which dataset answered, and at what version. Printed on screen and
    #: recorded against the dispensing, because "checked" is only
    #: meaningful if you can say checked against what.
    provider: str = ""
    dataset_version: str = ""

    @property
    def was_checked(self) -> bool:
        return self.state != CheckState.NOT_AVAILABLE

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "provider": self.provider,
            "dataset_version": self.dataset_version,
            "alerts": [alert.as_dict() for alert in self.alerts],
        }


@runtime_checkable
class InteractionProvider(Protocol):
    """What a licensed dataset must implement to plug in.

    Deliberately narrow. A provider is handed the ingredients being
    dispensed together and returns pairs it grades as interacting; it is
    not given the patient, because an interaction dataset grades the
    combination and the pharmacist applies the patient.
    """

    name: str
    dataset_version: str

    def interactions(self, ingredients: list[str]) -> list[dict]:
        """Pairs graded as interacting.

        Each entry: `{"a": str, "b": str, "severity": str,
        "summary": str, "reference": str}`.
        """
        ...


class NoDatasetProvider:
    """The default. Reports that the check did not run.

    It does not return an empty list of interactions, because an empty
    list is an answer and this provider has none. Everything downstream
    branches on `state`, so the absence stays visible all the way to the
    screen instead of decaying into apparent safety.
    """

    name = "none"
    dataset_version = ""

    def interactions(self, ingredients: list[str]) -> list[dict]:
        raise NotImplementedError("No interaction dataset is licensed.")


def get_provider() -> InteractionProvider:
    path = getattr(settings, "CLINICAL_INTERACTION_PROVIDER", "")
    if not path:
        return NoDatasetProvider()
    return import_string(path)()


#: How a provider's severity grading maps onto ours. Anything a dataset
#: grades as contraindicated is still a **warning**, not a critical: a
#: pharmacist who knows the patient may have a documented reason, and a
#: hard stop would be worked around rather than heeded. What the system
#: insists on is that it was seen and recorded.
_SEVERITY = {
    "CONTRAINDICATED": Severity.WARNING,
    "MAJOR": Severity.WARNING,
    "MODERATE": Severity.WARNING,
    "MINOR": Severity.INFO,
}


def check(*, products, as_of=None) -> InteractionResult:
    """Grade the combination being dispensed.

    Returns `NOT_AVAILABLE` when no dataset is configured — which is the
    shipped default, and the state the interface must print.
    """
    from sales.clinical import ingredients_of

    provider = get_provider()

    ingredients = sorted(
        {
            ingredient
            for product in products
            for ingredient in ingredients_of(product, as_of=as_of)
        }
    )
    if len(ingredients) < 2:
        # Nothing to interact with. Still reported as checked when a
        # dataset is present, so the screen does not flip to "not
        # checked" merely because the basket has one item.
        return InteractionResult(
            state=(
                CheckState.CLEAR
                if not isinstance(provider, NoDatasetProvider)
                else CheckState.NOT_AVAILABLE
            ),
            alerts=[],
            provider=provider.name,
            dataset_version=provider.dataset_version,
        )

    try:
        pairs = provider.interactions(ingredients)
    except NotImplementedError:
        return InteractionResult(
            state=CheckState.NOT_AVAILABLE,
            alerts=[],
            provider=provider.name,
            dataset_version=provider.dataset_version,
        )

    by_ingredient = {
        ingredient: product
        for product in products
        for ingredient in ingredients_of(product, as_of=as_of)
    }

    alerts = [
        about(
            by_ingredient.get(pair["a"]),
            code="DRUG_INTERACTION",
            severity=_SEVERITY.get(pair.get("severity", "MODERATE"), Severity.WARNING),
            title=f"{pair['a']} with {pair['b']}",
            detail=pair.get("summary", ""),
            meta={
                "a": pair["a"],
                "b": pair["b"],
                "grading": pair.get("severity", ""),
                "reference": pair.get("reference", ""),
                "provider": provider.name,
                "dataset_version": provider.dataset_version,
            },
        )
        for pair in pairs
    ]

    return InteractionResult(
        state=CheckState.FOUND if alerts else CheckState.CLEAR,
        alerts=alerts,
        provider=provider.name,
        dataset_version=provider.dataset_version,
    )


#: What the interface prints when nothing is licensed. Stated plainly,
#: because `docs/29` §3.2 requires the product not to claim interaction
#: checking it does not perform.
NOT_CHECKED_NOTICE = (
    "Interactions not checked. No clinical dataset is licensed on this "
    "installation."
)
