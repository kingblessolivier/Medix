"""Clinical checks at the counter.

**Every check here is a data match, not a judgement.** The system
compares two recorded values and reports that they conflict; a
registered pharmacist decides what to do about it. That distinction is
what keeps this the right side of `CLAUDE.md`'s "no clinical advice",
and it is why every threshold is a stored, sourced, effective-dated
attribute rather than a constant in this file.

All four surface as **warnings addressed to the pharmacist**, never
criticals. A pharmacist who knows the patient may have a good reason to
proceed, and a hard stop would either be overridden by a workaround or
would deny a legitimate dispensing. What the system is entitled to do is
insist the conflict was seen and recorded.

Drug–drug interaction is **not here**. See `sales/interactions.py`.

See docs/29-alerts.md §3.
"""

from __future__ import annotations

from datetime import date

from django.db import models
from django.utils import timezone

from catalog.models import ClinicalAttribute, ClinicalAttributeKind
from core.alerts import Alert, Severity, about


def _attributes(
    *, product, kind: str, as_of: date | None = None
) -> list[ClinicalAttribute]:
    """The values in force on `as_of`.

    A product can carry several of a kind — two active ingredients, for
    instance — so this returns a list rather than one row.
    """
    as_of = as_of or timezone.localdate()
    return list(
        ClinicalAttribute.objects.filter(
            product=product, kind=kind, effective_from__lte=as_of
        ).filter(
            models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=as_of)
        )
    )


def _one(*, product, kind: str, as_of: date | None = None) -> ClinicalAttribute | None:
    found = _attributes(product=product, kind=kind, as_of=as_of)
    return found[0] if found else None


def ingredients_of(product, *, as_of: date | None = None) -> set[str]:
    """Every active ingredient recorded for a product, normalised.

    Falls back to the generic name when no ingredient is recorded — for a
    single-ingredient product they are usually the same string, and
    matching on it is better than not matching at all. A combination
    product with no recorded ingredients matches on its generic name
    only, which is a gap the reference data closes rather than the code.
    """
    recorded = {
        attribute.value_text.strip().lower()
        for attribute in _attributes(
            product=product, kind=ClinicalAttributeKind.ACTIVE_INGREDIENT, as_of=as_of
        )
        if attribute.value_text.strip()
    }
    if recorded:
        return recorded
    return {product.generic_name.strip().lower()} if product.generic_name.strip() else set()


# --------------------------------------------------------------------------
# The four checks
# --------------------------------------------------------------------------


def allergy_contraindication(*, patient, product, as_of: date | None = None) -> list[Alert]:
    """A product whose ingredient the patient is recorded as allergic to.

    An equality between a pharmacist-recorded allergen and a
    pharmacist-recorded ingredient. We assert the match; we do not decide
    what it means.

    Matching is on the ingredient, so a patient allergic to amoxicillin
    is warned about it under every trade name.
    """
    if patient is None:
        return []

    allergens = {
        allergy.allergen_normalised: allergy for allergy in patient.allergies.all()
    }
    if not allergens:
        return []

    found = []
    for ingredient in ingredients_of(product, as_of=as_of):
        allergy = allergens.get(ingredient)
        if allergy is None:
            continue
        found.append(
            about(
                product,
                code="ALLERGY_CONTRAINDICATION",
                severity=Severity.WARNING,
                title=f"{patient.full_name} is recorded allergic to {allergy.allergen}",
                detail=(
                    f"{product.name} contains {ingredient}. "
                    f"Severity recorded as {allergy.get_severity_display().lower()}."
                ),
                meta={
                    "allergen": allergy.allergen,
                    "severity": allergy.severity,
                    "recorded_on": allergy.recorded_on.isoformat(),
                },
            )
        )
    return found


def duplicate_therapy(*, products) -> list[Alert]:
    """Two products from one therapeutic category on the same dispensing.

    A count, not a clinical opinion. It is often deliberate — two
    antibiotics in combination therapy is normal — which is exactly why
    it is a warning the pharmacist clears rather than a refusal.

    Products with no category are skipped: an uncategorised pair would
    match every other uncategorised product and turn this into noise.
    """
    by_category: dict = {}
    for product in products:
        if product.category_id is None:
            continue
        by_category.setdefault(product.category_id, []).append(product)

    found = []
    for group in by_category.values():
        if len(group) < 2:
            continue
        names = ", ".join(sorted(product.name for product in group))
        found.append(
            about(
                group[0],
                code="DUPLICATE_THERAPY",
                severity=Severity.WARNING,
                title=f"Two products in {group[0].category.name}",
                detail=names,
                meta={"products": sorted(product.name for product in group)},
            )
        )
    return found


def demographic_restriction(
    *, patient, product, as_of: date | None = None
) -> list[Alert]:
    """An age or pregnancy restriction recorded against the product.

    Only fires on a **recorded** restriction. Nothing is inferred from a
    drug class, and a product with no recorded limits produces nothing —
    silence here means "no restriction on file", never "safe".
    """
    if patient is None:
        return []

    found = []
    minimum = _one(
        product=product, kind=ClinicalAttributeKind.MIN_AGE_YEARS, as_of=as_of
    )
    maximum = _one(
        product=product, kind=ClinicalAttributeKind.MAX_AGE_YEARS, as_of=as_of
    )
    age = patient.age_years(as_of=as_of)

    if (minimum or maximum) and age is None:
        # The check could not run. Saying so is the point: a restriction
        # skipped because nobody recorded a birth date must not look like
        # a restriction that passed.
        found.append(
            about(
                product,
                code="DEMOGRAPHIC_UNCHECKED",
                severity=Severity.WARNING,
                title=f"{product.name} has an age restriction, age not recorded",
                detail="Record the patient's date of birth to check it.",
                meta={"reason": "no_date_of_birth"},
            )
        )
    else:
        if minimum is not None and age is not None and age < minimum.value_number:
            found.append(
                about(
                    product,
                    code="DEMOGRAPHIC_RESTRICTION",
                    severity=Severity.WARNING,
                    title=f"{product.name} is restricted under {minimum.value_number}",
                    detail=f"Patient is {age}. Source: {minimum.source}.",
                    meta={"limit": minimum.value_number, "age": age, "source": minimum.source},
                )
            )
        if maximum is not None and age is not None and age > maximum.value_number:
            found.append(
                about(
                    product,
                    code="DEMOGRAPHIC_RESTRICTION",
                    severity=Severity.WARNING,
                    title=f"{product.name} is restricted over {maximum.value_number}",
                    detail=f"Patient is {age}. Source: {maximum.source}.",
                    meta={"limit": maximum.value_number, "age": age, "source": maximum.source},
                )
            )

    pregnancy = _one(
        product=product, kind=ClinicalAttributeKind.PREGNANCY_RESTRICTED, as_of=as_of
    )
    if pregnancy is not None and pregnancy.value_number and patient.is_pregnant:
        found.append(
            about(
                product,
                code="PREGNANCY_RESTRICTION",
                severity=Severity.WARNING,
                title=f"{product.name} is restricted in pregnancy",
                detail=f"Source: {pregnancy.source}.",
                meta={"source": pregnancy.source},
            )
        )
    return found


def maximum_daily_dose(
    *, product, quantity_base: int, days: int, as_of: date | None = None
) -> list[Alert]:
    """Quantity dispensed against a recorded daily maximum.

    **The system does not calculate a dose.** It divides a quantity by a
    number of days and compares the result to a stored limit. Working out
    what dose a patient should take is the prescriber's job and the
    pharmacist's check, and nothing here attempts it.

    `days` of zero or less means the duration is unknown, and the check
    does not run rather than dividing by a guess.
    """
    if days <= 0:
        return []

    limit = _one(
        product=product, kind=ClinicalAttributeKind.MAX_DAILY_DOSE_BASE, as_of=as_of
    )
    if limit is None or not limit.value_number:
        return []

    per_day = quantity_base // days
    if per_day <= limit.value_number:
        return []

    return [
        about(
            product,
            code="MAXIMUM_DAILY_DOSE",
            severity=Severity.WARNING,
            title=f"{product.name} exceeds its recorded daily maximum",
            detail=(
                f"{per_day:,} units a day over {days} days, "
                f"against a maximum of {limit.value_number:,}. Source: {limit.source}."
            ),
            meta={
                "per_day": per_day,
                "limit": limit.value_number,
                "days": days,
                "source": limit.source,
            },
        )
    ]


def for_dispensing(
    *,
    patient,
    products,
    as_of: date | None = None,
) -> list[Alert]:
    """Every check that applies to one dispensing, in one call.

    Interaction checking is **absent by design** and its state is
    reported separately by `sales.interactions.state()`, so a screen can
    say that it was not performed rather than showing nothing and letting
    the pharmacist infer it was.
    """
    found: list[Alert] = []
    for product in products:
        found.extend(
            allergy_contraindication(patient=patient, product=product, as_of=as_of)
        )
        found.extend(
            demographic_restriction(patient=patient, product=product, as_of=as_of)
        )
    found.extend(duplicate_therapy(products=products))
    return found
