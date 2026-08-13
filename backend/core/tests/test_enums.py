"""The frontend's hardcoded choice values, checked against the models.

Twice now a screen has offered a value the server does not accept —
`RETAIL_PHARMACY` when the stored value is `RETAIL`, and `FRIDGE` for a
location kind that has only `BRANCH` and `STORE`. Both were written by
reading the Python *attribute* name instead of its value, both typecheck
perfectly, and both surface as a form that fills in correctly and is
rejected on submit.

There is no shared schema between the two languages, and generating one
for four Rwanda FDA licence types would be more machinery than the
problem deserves. So instead: the frontend keeps its literals, and this
test reads them back and fails when they drift.

Adding a choice to a model does not fail here — the frontend simply does
not offer it yet, which is a decision rather than a bug. What fails is
offering something the server will refuse.
"""

from __future__ import annotations

import pathlib
import re

import pytest

FRONTEND = pathlib.Path(__file__).resolve().parents[3] / "frontend" / "src"

#: Each entry: where the literals live, and the model choices they mirror.
#: The tuple regex matches `["VALUE", "Label"]` pairs inside a `const X = [`.
MIRRORS = [
    (
        "modules/compliance/ComplianceScreen.tsx",
        "KINDS",
        "core.models.LicenceKind",
    ),
    (
        "modules/settings/SettingsScreen.tsx",
        "LOCATION_KINDS",
        "inventory.models.LocationKind",
    ),
    (
        "modules/settings/SettingsScreen.tsx",
        "TEMPERATURES",
        "inventory.models.TemperatureClass",
    ),
    (
        "modules/catalogue/ProductEditor.tsx",
        "LEGAL",
        "catalog.models.LegalStatus",
    ),
    (
        "modules/catalogue/ProductEditor.tsx",
        "TAX",
        "catalog.models.TaxTreatment",
    ),
    (
        "modules/insurance/SchemesScreen.tsx",
        "MODELS",
        "insurance.models.ReimbursementModel",
    ),
    (
        "modules/insurance/SchemesScreen.tsx",
        "PERIODS",
        "insurance.models.CapitationPeriod",
    ),
    (
        "modules/insurance/SchemesScreen.tsx",
        "SCOPES",
        "insurance.models.CoverageScope",
    ),
    (
        "modules/inventory/InventoryScreen.tsx",
        "DISPOSAL_REASONS",
        "finance.models.WriteOffReason",
    ),
    (
        "modules/receiving/ImportReceiptScreen.tsx",
        "IMPORT_KINDS",
        "commerce.models.ImportDocumentKind",
    ),
]


def literals(path: pathlib.Path, name: str) -> list[str]:
    """The first element of every pair in `const <name> = [ ... ]`."""
    source = path.read_text(encoding="utf-8")
    block = re.search(rf"const {name} = \[(.*?)\] as const;", source, re.S)
    assert block, f"{name} not found in {path.name}"
    return re.findall(r'\[\s*"([^"]+)"', block.group(1))


def choices(dotted: str) -> set[str]:
    from django.utils.module_loading import import_string

    return set(import_string(dotted).values)


@pytest.mark.parametrize("relative,name,dotted", MIRRORS)
def test_the_screen_offers_only_values_the_server_accepts(relative, name, dotted):
    path = FRONTEND / relative
    if not path.exists():  # pragma: no cover - the screen was renamed
        pytest.skip(f"{relative} no longer exists")

    offered = literals(path, name)
    accepted = choices(dotted)

    invented = [value for value in offered if value not in accepted]
    assert invented == [], (
        f"{relative} offers {invented} for {dotted}, which the server refuses. "
        f"It accepts {sorted(accepted)}. Use the stored value, not the Python "
        f"attribute name."
    )


@pytest.mark.parametrize("relative,name,dotted", MIRRORS)
def test_the_screen_offers_something(relative, name, dotted):
    """A list that silently emptied is its own failure."""
    path = FRONTEND / relative
    if not path.exists():  # pragma: no cover
        pytest.skip(f"{relative} no longer exists")
    assert literals(path, name), f"{name} in {relative} is empty"
