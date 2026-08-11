"""Quantity as an integer paired with a unit of measure.

A bare integer quantity is meaningless in this system: a pack is not a
tablet. Every quantity carries its unit, and the ledger stores base units
only.

Conversion is integer-only. A fractional base unit is a modelling error,
not a rounding problem.

See docs/03-data-model.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class UomLike(Protocol):
    """Anything that can act as a unit of measure in a conversion."""

    code: str
    factor_to_base: int


class UomMismatch(ValueError):
    """Raised when combining quantities of different products or chains."""


class FractionalBaseUnit(ValueError):
    """Raised when a conversion would produce a fraction of a base unit."""


@dataclass(frozen=True, slots=True)
class Quantity:
    """An amount expressed in a specific unit of measure."""

    value: int
    uom: UomLike

    def __post_init__(self) -> None:
        if not isinstance(self.value, int) or isinstance(self.value, bool):
            raise TypeError(f"Quantity.value must be int, got {type(self.value).__name__}")
        if self.uom.factor_to_base < 1:
            raise ValueError("factor_to_base must be >= 1")

    @property
    def base_value(self) -> int:
        """This quantity expressed in base units."""
        return self.value * self.uom.factor_to_base

    def to(self, target: UomLike) -> Quantity:
        """Convert to ``target``, refusing any fractional result."""
        base = self.base_value
        if base % target.factor_to_base:
            raise FractionalBaseUnit(
                f"{self.value} {self.uom.code} is {base} base units, "
                f"which is not a whole number of {target.code}"
            )
        return Quantity(base // target.factor_to_base, target)

    def _check(self, other: Quantity) -> None:
        if self.uom.code != other.uom.code:
            raise UomMismatch(f"Cannot combine {self.uom.code} and {other.uom.code} directly")

    def __add__(self, other: Quantity) -> Quantity:
        self._check(other)
        return Quantity(self.value + other.value, self.uom)

    def __sub__(self, other: Quantity) -> Quantity:
        self._check(other)
        return Quantity(self.value - other.value, self.uom)

    def __mul__(self, factor: int) -> Quantity:
        if not isinstance(factor, int) or isinstance(factor, bool):
            raise TypeError("Quantity may only be multiplied by an int")
        return Quantity(self.value * factor, self.uom)

    __rmul__ = __mul__

    def __neg__(self) -> Quantity:
        return Quantity(-self.value, self.uom)

    def __lt__(self, other: Quantity) -> bool:
        return self.base_value < other.base_value

    def __le__(self, other: Quantity) -> bool:
        return self.base_value <= other.base_value

    def __gt__(self, other: Quantity) -> bool:
        return self.base_value > other.base_value

    def __ge__(self, other: Quantity) -> bool:
        return self.base_value >= other.base_value

    @property
    def is_zero(self) -> bool:
        return self.value == 0

    def __str__(self) -> str:
        return f"{self.value:,} {self.uom.code}"

    def __repr__(self) -> str:
        return f"Quantity({self.value}, {self.uom.code!r})"


def from_base(base_value: int, uom: UomLike) -> Quantity:
    """Build a Quantity in ``uom`` from a base-unit amount."""
    if base_value % uom.factor_to_base:
        raise FractionalBaseUnit(
            f"{base_value} base units is not a whole number of {uom.code}"
        )
    return Quantity(base_value // uom.factor_to_base, uom)


def split_to_units(base_value: int, chain: list[UomLike]) -> list[Quantity]:
    """Break a base amount into the largest whole units available.

    ``chain`` is ordered largest factor first. 1,240 capsules with a chain
    of pack(1000) and unit(1) becomes ``[1 PACK, 240 UNIT]`` — which is how
    a pharmacist reads stock.
    """
    remaining = base_value
    out: list[Quantity] = []
    for uom in sorted(chain, key=lambda u: -u.factor_to_base):
        whole, remaining = divmod(remaining, uom.factor_to_base)
        if whole:
            out.append(Quantity(whole, uom))
    if remaining:
        raise FractionalBaseUnit(f"{remaining} base units left over; chain lacks a factor-1 unit")
    return out
