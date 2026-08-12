"""Deriving a price for one packaging level from another.

A depot prices a carton. A counter sells a tablet. Somewhere between the
two, someone has to divide — and the division rarely comes out whole.

A carton of 1,200 tablets at 28,000 RWF is 23.33 RWF a tablet. RWF has no
sub-unit in practice, so the price of one tablet is either 23 or 24, and
the choice is not cosmetic:

    23 × 1,200 = 27,600   — 400 short of what the carton cost
    24 × 1,200 = 28,800   — 800 over

**Derived unit prices round up.** Selling below the price you bought at,
on every one of twelve hundred tablets, is a loss the pharmacy never sees
because each individual sale looks right. Rounding up also matches how
loose sales actually work: buying one tablet is dearer per tablet than
buying the pack, everywhere in the world.

The pack price stays the authoritative figure. A derived price is a
convenience for the counter, never a new source of truth — which is why
`derive` returns the rounding loss alongside the price, so a screen can
show what it did instead of hiding it.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.money import Money
from core.quantity import UomLike


class NotSameProduct(ValueError):
    """Two units from different packaging chains cannot be compared."""


@dataclass(frozen=True)
class DerivedPrice:
    """A price at one level, and what the arithmetic cost to get there."""

    price: Money
    #: Units of `to_uom` in one `from_uom`. 1,200 tablets in a carton.
    per: int
    #: price × per, minus the original. Zero when the division was exact.
    rounding_gain: Money

    @property
    def is_exact(self) -> bool:
        return self.rounding_gain.is_zero


def derive(price: Money, *, from_uom: UomLike, to_uom: UomLike) -> DerivedPrice:
    """Price of one `to_uom`, given the price of one `from_uom`.

    Works in both directions: a carton price down to a tablet, or a tablet
    price up to a carton. Going up is exact by construction; going down
    rounds up, for the reason in the module docstring.
    """
    if from_uom.factor_to_base <= 0 or to_uom.factor_to_base <= 0:
        raise ValueError("A unit of measure must have a positive factor.")

    if to_uom.factor_to_base >= from_uom.factor_to_base:
        # Going up: a whole number of the smaller unit fits the larger, so
        # multiplication is exact and there is nothing to round.
        per, remainder = divmod(to_uom.factor_to_base, from_uom.factor_to_base)
        if remainder:
            raise NotSameProduct(
                f"{to_uom.code} is not a whole multiple of {from_uom.code}"
            )
        return DerivedPrice(
            price=price * per,
            per=per,
            rounding_gain=Money.zero(price.currency),
        )

    per, remainder = divmod(from_uom.factor_to_base, to_uom.factor_to_base)
    if remainder:
        raise NotSameProduct(f"{from_uom.code} is not a whole multiple of {to_uom.code}")

    # Ceiling division on integers, without floating point anywhere near
    # money: -(-a // b) is the exact ceiling for positive b.
    minor = -(-price.amount // per)
    unit = Money(minor, price.currency)
    return DerivedPrice(
        price=unit,
        per=per,
        rounding_gain=(unit * per) - price,
    )


def price_list(price: Money, *, priced_uom: UomLike, chain: list[UomLike]) -> dict[str, DerivedPrice]:
    """Every level's price, from one priced level.

    What a product screen shows after someone types the carton cost: the
    pack, the blister and the tablet all fall out of it, and each carries
    its own rounding so nothing is quietly absorbed.
    """
    return {
        uom.code: derive(price, from_uom=priced_uom, to_uom=uom)
        for uom in sorted(chain, key=lambda u: -u.factor_to_base)
    }


def cost_per_base(price: Money, *, priced_uom: UomLike) -> int:
    """Unit cost in base units, for batch costing.

    Batch cost is stored per base unit and must not inherit the sell-side
    rounding-up rule — overstating cost understates margin on every line
    it touches. This truncates instead, and the remainder stays visible in
    the difference between the batch value and what was actually paid.
    """
    return price.amount // priced_uom.factor_to_base
