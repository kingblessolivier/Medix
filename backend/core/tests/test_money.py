"""Money is on the mandatory-test list. See docs/15-testing.md."""

import pytest
from decimal import Decimal

from core.money import CurrencyMismatch, Money


class TestConstruction:
    def test_rejects_float(self):
        with pytest.raises(TypeError):
            Money(28000.50)

    def test_rejects_bool(self):
        with pytest.raises(TypeError):
            Money(True)

    def test_rejects_unknown_currency(self):
        with pytest.raises(ValueError):
            Money(100, "XYZ")

    def test_from_major_rwf_has_no_minor_unit(self):
        assert Money.from_major(28000).amount == 28000

    def test_from_major_usd_has_cents(self):
        assert Money.from_major(Decimal("12.50"), "USD").amount == 1250

    def test_from_major_rounds_half_up(self):
        assert Money.from_major(Decimal("12.005"), "USD").amount == 1201


class TestArithmetic:
    def test_add(self):
        assert Money(28000) + Money(12000) == Money(40000)

    def test_subtract(self):
        assert Money(28000) - Money(12000) == Money(16000)

    def test_multiply_by_int(self):
        assert Money(28000) * 2 == Money(56000)

    def test_multiply_by_float_raises(self):
        with pytest.raises(TypeError):
            Money(28000) * 1.5

    def test_currency_mismatch_raises_on_add(self):
        with pytest.raises(CurrencyMismatch):
            Money(100, "RWF") + Money(100, "USD")

    def test_currency_mismatch_raises_on_compare(self):
        with pytest.raises(CurrencyMismatch):
            Money(100, "RWF") < Money(100, "USD")

    def test_no_float_ever_appears(self):
        total = Money.zero()
        for _ in range(1000):
            total = total + Money.from_major(1)
        assert total.amount == 1000
        assert isinstance(total.amount, int)


class TestAllocate:
    """Landed-cost apportionment must not lose or invent a franc."""

    def test_even_split(self):
        parts = Money(300).allocate([1, 1, 1])
        assert parts == [Money(100), Money(100), Money(100)]

    def test_uneven_split_sums_exactly(self):
        parts = Money(320000).allocate([100, 200, 75])
        assert sum(p.amount for p in parts) == 320000

    def test_indivisible_remainder_sums_exactly(self):
        parts = Money(100).allocate([1, 1, 1])
        assert sum(p.amount for p in parts) == 100
        assert sorted(p.amount for p in parts) == [33, 33, 34]

    def test_remainder_goes_to_largest_weight_first(self):
        parts = Money(10).allocate([1, 2])
        assert sum(p.amount for p in parts) == 10
        assert parts[1].amount >= parts[0].amount

    @pytest.mark.parametrize(
        "total,weights",
        [
            (425, [100, 50, 200, 75]),
            (2400000, [100, 50, 200, 75]),
            (1, [1, 1, 1, 1]),
            (999999, [7, 11, 13]),
        ],
    )
    def test_always_sums_to_total(self, total, weights):
        parts = Money(total).allocate(weights)
        assert sum(p.amount for p in parts) == total

    def test_zero_weights_raise(self):
        with pytest.raises(ValueError):
            Money(100).allocate([0, 0])

    def test_negative_weight_raises(self):
        with pytest.raises(ValueError):
            Money(100).allocate([1, -1])


class TestDisplay:
    def test_rwf_has_no_decimals(self):
        assert str(Money(28000)) == "RWF 28,000"

    def test_usd_has_two_decimals(self):
        assert str(Money(1250, "USD")) == "USD 12.50"
