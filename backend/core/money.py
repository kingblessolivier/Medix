"""Money as integer minor units with an explicit currency.

There is no float anywhere on the money path. Adding two different
currencies raises rather than silently coercing.

See docs/03-data-model.md and docs/24-database.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

# Currencies whose smallest circulating unit is the major unit. RWF has a
# nominal centime that is not used in practice, so retail amounts are whole
# francs; financial reports may still carry sub-unit precision.
MINOR_UNITS = {"RWF": 0, "USD": 2, "EUR": 2, "KES": 2, "UGX": 0, "TZS": 2}

DEFAULT_CURRENCY = "RWF"


class CurrencyMismatch(ValueError):
    """Raised when an operation mixes two currencies."""


@dataclass(frozen=True, slots=True, order=False)
class Money:
    """An exact monetary amount.

    ``amount`` is always in minor units. For RWF the minor unit is the franc.
    """

    amount: int
    currency: str = DEFAULT_CURRENCY

    def __post_init__(self) -> None:
        if not isinstance(self.amount, int) or isinstance(self.amount, bool):
            raise TypeError(f"Money.amount must be int minor units, got {type(self.amount).__name__}")
        if self.currency not in MINOR_UNITS:
            raise ValueError(f"Unknown currency {self.currency!r}")

    # -- construction ------------------------------------------------------

    @classmethod
    def zero(cls, currency: str = DEFAULT_CURRENCY) -> Money:
        return cls(0, currency)

    @classmethod
    def from_major(cls, value: Decimal | int | str, currency: str = DEFAULT_CURRENCY) -> Money:
        """Build from a major-unit value, e.g. ``28000`` RWF or ``12.50`` USD."""
        exponent = MINOR_UNITS[currency]
        quantized = (Decimal(str(value)) * (10**exponent)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        return cls(int(quantized), currency)

    # -- arithmetic --------------------------------------------------------

    def _check(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(f"Cannot combine {self.currency} and {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: int) -> Money:
        if not isinstance(factor, int) or isinstance(factor, bool):
            raise TypeError("Money may only be multiplied by an int")
        return Money(self.amount * factor, self.currency)

    __rmul__ = __mul__

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._check(other)
        return self.amount >= other.amount

    # -- helpers -----------------------------------------------------------

    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    @property
    def is_negative(self) -> bool:
        return self.amount < 0

    def to_major(self) -> Decimal:
        exponent = MINOR_UNITS[self.currency]
        return Decimal(self.amount) / (10**exponent)

    def allocate(self, weights: Iterable[int]) -> list[Money]:
        """Split exactly across ``weights``, losing nothing to rounding.

        Used for landed-cost apportionment across consolidated import
        participants. The sum of the result always equals ``self``.
        """
        weights = list(weights)
        if not weights:
            raise ValueError("allocate() requires at least one weight")
        if any(w < 0 for w in weights):
            raise ValueError("weights must be non-negative")
        total = sum(weights)
        if total == 0:
            raise ValueError("weights must not sum to zero")

        shares = [self.amount * w // total for w in weights]
        remainder = self.amount - sum(shares)
        # Distribute the remainder one minor unit at a time, largest weight
        # first, so the split is deterministic and sums exactly.
        order = sorted(range(len(weights)), key=lambda i: (-weights[i], i))
        for i in range(remainder):
            shares[order[i % len(order)]] += 1
        return [Money(s, self.currency) for s in shares]

    def __str__(self) -> str:
        exponent = MINOR_UNITS[self.currency]
        if exponent == 0:
            return f"{self.currency} {self.amount:,}"
        return f"{self.currency} {self.to_major():,.{exponent}f}"

    def __repr__(self) -> str:
        return f"Money({self.amount}, {self.currency!r})"
