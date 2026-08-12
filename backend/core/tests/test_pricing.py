"""Deriving one packaging level's price from another.

The rounding rule is a commercial decision, not an implementation detail:
a derived unit price that rounds *down* loses money on every one of a
thousand tablets, invisibly, because each sale looks correct.
"""

from dataclasses import dataclass

import pytest

from core.money import Money
from core.pricing import DerivedPrice, NotSameProduct, cost_per_base, derive, price_list
from core.quantity import compose


@dataclass(frozen=True)
class Uom:
    code: str
    factor_to_base: int


CARTON = Uom("CARTON", 1200)
PACK = Uom("PACK", 100)
BLISTER = Uom("BLISTER", 10)
TABLET = Uom("TABLET", 1)
CHAIN = [CARTON, PACK, BLISTER, TABLET]


class TestDerive:
    def test_carton_price_derives_a_pack_price(self):
        result = derive(Money(28000), from_uom=CARTON, to_uom=PACK)
        assert result.per == 12
        # 2,333.33 a pack, rounded up.
        assert result.price == Money(2334)
        assert not result.is_exact

    def test_clean_division_is_exact(self):
        result = derive(Money(12000), from_uom=CARTON, to_uom=PACK)
        assert result.price == Money(1000)
        assert result.is_exact
        assert result.rounding_gain == Money(0)

    def test_derived_unit_price_never_falls_below_cost(self):
        """The rule this module exists for.

        28,000 over 1,200 tablets is 23.33. At 23 the pharmacy is 400
        short by the time the carton is gone, and no single sale shows it.
        """
        result = derive(Money(28000), from_uom=CARTON, to_uom=TABLET)
        assert result.price == Money(24)
        assert result.price * result.per >= Money(28000)
        assert result.rounding_gain == Money(800)

    def test_going_up_is_exact_by_construction(self):
        result = derive(Money(24), from_uom=TABLET, to_uom=CARTON)
        assert result.price == Money(28800)
        assert result.is_exact

    def test_same_unit_is_identity(self):
        result = derive(Money(28000), from_uom=CARTON, to_uom=CARTON)
        assert result.price == Money(28000)
        assert result.per == 1
        assert result.is_exact

    def test_incompatible_chain_is_refused(self):
        odd = Uom("ODD", 7)
        with pytest.raises(NotSameProduct):
            derive(Money(1000), from_uom=CARTON, to_uom=odd)

    def test_one_franc_stays_one_franc(self):
        """Ceiling means a cheap product never derives to zero.

        A price of zero would let stock leave the building for nothing.
        """
        result = derive(Money(1), from_uom=CARTON, to_uom=TABLET)
        assert result.price == Money(1)
        assert not result.price.is_zero


class TestPriceList:
    def test_every_level_is_priced_from_one_entry(self):
        prices = price_list(Money(28000), priced_uom=CARTON, chain=CHAIN)
        assert set(prices) == {"CARTON", "PACK", "BLISTER", "TABLET"}
        assert prices["CARTON"].price == Money(28000)
        assert prices["TABLET"].price == Money(24)

    def test_smaller_units_are_dearer_per_unit(self):
        """Buying loose costs more per tablet than buying the pack.

        True of every pharmacy counter, and a direct consequence of
        rounding each level up independently.
        """
        prices = price_list(Money(28000), priced_uom=CARTON, chain=CHAIN)
        per_tablet_via_carton = prices["CARTON"].price.amount / 1200
        per_tablet_loose = prices["TABLET"].price.amount
        assert per_tablet_loose >= per_tablet_via_carton


class TestCostPerBase:
    def test_batch_cost_truncates_rather_than_rounds_up(self):
        """Cost must not inherit the sell-side rule.

        Rounding cost up overstates it on every line, which understates
        margin everywhere it is used.
        """
        assert cost_per_base(Money(28000), priced_uom=CARTON) == 23

    def test_exact_cost_is_unchanged(self):
        assert cost_per_base(Money(12000), priced_uom=CARTON) == 10


class TestCompose:
    def test_mixed_entry_becomes_base_units(self):
        """"Ten cartons and eight tablets" is how a clerk counts."""
        assert compose([(10, CARTON), (8, TABLET)]) == 12008

    def test_order_and_repetition_do_not_matter(self):
        assert compose([(8, TABLET), (10, CARTON)]) == 12008
        assert compose([(5, CARTON), (5, CARTON)]) == compose([(10, CARTON)])

    def test_empty_entry_is_zero(self):
        assert compose([]) == 0

    def test_negative_count_is_refused(self):
        # Direction belongs to the movement, never to the tally.
        with pytest.raises(ValueError):
            compose([(-1, CARTON)])

    def test_round_trips_with_split(self):
        from core.quantity import split_to_units

        base = compose([(3, CARTON), (2, PACK), (4, BLISTER), (7, TABLET)])
        parts = split_to_units(base, CHAIN)
        assert [(q.value, q.uom.code) for q in parts] == [
            (3, "CARTON"),
            (2, "PACK"),
            (4, "BLISTER"),
            (7, "TABLET"),
        ]
