"""Unit of measure is on the mandatory-test list. See docs/15-testing.md."""

from dataclasses import dataclass

import pytest

from core.quantity import (
    FractionalBaseUnit,
    Quantity,
    UomMismatch,
    from_base,
    split_to_units,
)


@dataclass(frozen=True)
class Uom:
    code: str
    factor_to_base: int


# Amoxicillin 500mg: carton of 12 packs, pack of 100 capsules, blister of 10.
CARTON = Uom("CARTON", 1200)
PACK = Uom("PACK", 100)
BLISTER = Uom("BLISTER", 10)
UNIT = Uom("UNIT", 1)
CHAIN = [CARTON, PACK, BLISTER, UNIT]


class TestConstruction:
    def test_rejects_float(self):
        with pytest.raises(TypeError):
            Quantity(6.5, UNIT)

    def test_rejects_bool(self):
        with pytest.raises(TypeError):
            Quantity(True, UNIT)

    def test_base_value(self):
        assert Quantity(2, PACK).base_value == 200


class TestConversion:
    def test_pack_to_units(self):
        assert Quantity(1, PACK).to(UNIT) == Quantity(100, UNIT)

    def test_units_to_pack(self):
        assert Quantity(100, UNIT).to(PACK) == Quantity(1, PACK)

    def test_carton_to_packs(self):
        assert Quantity(1, CARTON).to(PACK) == Quantity(12, PACK)

    def test_round_trips_exactly(self):
        original = Quantity(3, CARTON)
        assert original.to(UNIT).to(CARTON) == original

    def test_fractional_conversion_raises(self):
        """6 capsules is not a whole number of packs."""
        with pytest.raises(FractionalBaseUnit):
            Quantity(6, UNIT).to(PACK)

    def test_never_produces_fractional_base_units(self):
        for value in range(1, 200):
            q = Quantity(value, BLISTER)
            assert q.base_value == value * 10
            assert isinstance(q.base_value, int)


class TestPartialPack:
    """Six tablets from a pack of a hundred is the normal case here."""

    def test_dispense_six_units(self):
        assert Quantity(6, UNIT).base_value == 6

    def test_purchase_packs_dispense_units_reconciles(self):
        received = Quantity(5, PACK).base_value
        dispensed = sum(Quantity(6, UNIT).base_value for _ in range(10))
        assert received - dispensed == 440

    def test_split_reads_as_a_pharmacist_would(self):
        """1,240 capsules is 1 carton, 4 blisters — not 1,240 loose units."""
        assert split_to_units(1240, CHAIN) == [
            Quantity(1, CARTON),
            Quantity(4, BLISTER),
        ]

    def test_split_omits_empty_levels(self):
        """1,200 capsules is exactly one carton and nothing else."""
        assert split_to_units(1200, CHAIN) == [Quantity(1, CARTON)]

    def test_split_sums_back_to_base(self):
        parts = split_to_units(1240, CHAIN)
        assert sum(p.base_value for p in parts) == 1240

    def test_split_without_unit_factor_raises(self):
        with pytest.raises(FractionalBaseUnit):
            split_to_units(1245, [CARTON, PACK, BLISTER])


class TestArithmetic:
    def test_add_same_uom(self):
        assert Quantity(2, PACK) + Quantity(3, PACK) == Quantity(5, PACK)

    def test_add_different_uom_raises(self):
        with pytest.raises(UomMismatch):
            Quantity(2, PACK) + Quantity(3, UNIT)

    def test_compare_across_uom_uses_base(self):
        assert Quantity(1, PACK) > Quantity(50, UNIT)
        assert Quantity(1, PACK) == Quantity(1, PACK)

    def test_multiply_by_float_raises(self):
        with pytest.raises(TypeError):
            Quantity(2, PACK) * 1.5


class TestFromBase:
    def test_exact(self):
        assert from_base(300, PACK) == Quantity(3, PACK)

    def test_inexact_raises(self):
        with pytest.raises(FractionalBaseUnit):
            from_base(305, PACK)
