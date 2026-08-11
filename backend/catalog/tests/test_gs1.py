"""GS1 parsing.

Tested hard because the failure modes are silent: a swallowed separator
produces a wrong batch number, and a mishandled DD=00 produces a wrong
expiry — in the direction that lets expired stock be sold.
"""

from datetime import date

import pytest

from catalog.gs1 import FNC1, GS1ParseError, parse

# A real-shaped element string: GTIN, expiry, batch, serial.
# 05012345678900 has a valid check digit.
GTIN = "05012345678900"
TODAY = date(2026, 8, 11)


def build(*parts: str) -> str:
    return "".join(parts)


class TestGtin:
    def test_parses(self):
        assert parse(f"01{GTIN}", today=TODAY).gtin == GTIN

    def test_rejects_bad_check_digit(self):
        with pytest.raises(GS1ParseError, match="check digit"):
            parse("0105012345678901", today=TODAY)

    def test_rejects_non_numeric(self):
        with pytest.raises(GS1ParseError):
            parse("010501234567890X", today=TODAY)

    def test_rejects_short(self):
        with pytest.raises(GS1ParseError, match="needs 14"):
            parse("0105012345", today=TODAY)


class TestExpiry:
    def test_parses_full_date(self):
        assert parse("17270408", today=TODAY).expiry_date == date(2027, 4, 8)

    def test_day_zero_means_last_day_of_month(self):
        """DD=00 is 'no specific day' and means end of month.

        Read naively this becomes day zero — either an exception or a
        silent shift into the previous month.
        """
        assert parse("17270400", today=TODAY).expiry_date == date(2027, 4, 30)

    def test_day_zero_february_leap_year(self):
        assert parse("17280200", today=TODAY).expiry_date == date(2028, 2, 29)

    def test_day_zero_february_non_leap_year(self):
        assert parse("17270200", today=TODAY).expiry_date == date(2027, 2, 28)

    def test_rejects_impossible_day(self):
        with pytest.raises(GS1ParseError, match="Day 31"):
            parse("17270431", today=TODAY)

    def test_rejects_impossible_month(self):
        with pytest.raises(GS1ParseError, match="Month 13"):
            parse("17271301", today=TODAY)

    def test_rejects_truncated(self):
        """Caught by the fixed-length check before the date is parsed."""
        with pytest.raises(GS1ParseError, match="needs 6 characters"):
            parse("172704", today=TODAY)

    def test_rejects_non_numeric_date(self):
        with pytest.raises(GS1ParseError, match="YYMMDD"):
            parse("1727O408", today=TODAY)


class TestYearWindow:
    def test_near_future_is_this_century(self):
        assert parse("17270408", today=TODAY).expiry_date.year == 2027

    def test_recent_past_is_this_century(self):
        assert parse("17240408", today=TODAY).expiry_date.year == 2024

    def test_far_two_digit_year_resolves_to_nearest_century(self):
        """'80 from 2026 is 1980, not 2080 — 1980 is nearer."""
        assert parse("17800408", today=TODAY).expiry_date.year == 1980

    def test_across_a_century_boundary(self):
        """From 2099, '01 means 2101 rather than 2001."""
        assert parse("17010408", today=date(2099, 1, 1)).expiry_date.year == 2101


class TestVariableLength:
    def test_batch_terminated_by_separator(self):
        pack = parse(build("10", "AMX-0021", FNC1, "17", "270408"), today=TODAY)
        assert pack.batch_number == "AMX-0021"
        assert pack.expiry_date == date(2027, 4, 8)

    def test_batch_at_end_needs_no_separator(self):
        pack = parse(build("17", "270408", "10", "AMX-0021"), today=TODAY)
        assert pack.batch_number == "AMX-0021"

    def test_without_separator_the_batch_swallows_the_rest(self):
        """Documents the failure this separator handling exists to avoid."""
        pack = parse(build("10", "AMX-0021", "17", "270408"), today=TODAY)
        assert pack.batch_number == "AMX-002117270408"
        assert pack.expiry_date is None

    def test_rejects_overlong_variable_field(self):
        with pytest.raises(GS1ParseError, match="maximum is 20"):
            parse(build("10", "X" * 21), today=TODAY)

    def test_serial_parsed(self):
        pack = parse(build("21", "SN12345678", FNC1, "17", "270408"), today=TODAY)
        assert pack.serial == "SN12345678"


class TestFullPack:
    def test_realistic_scan(self):
        """GTIN, expiry, batch, serial — the shape a real pack carries."""
        scanned = build("01", GTIN, "17", "270408", "10", "AMX-0021", FNC1, "21", "SN0001")
        pack = parse(scanned, today=TODAY)

        assert pack.gtin == GTIN
        assert pack.expiry_date == date(2027, 4, 8)
        assert pack.batch_number == "AMX-0021"
        assert pack.serial == "SN0001"
        assert pack.is_usable is True

    def test_fixed_length_field_may_still_be_followed_by_a_separator(self):
        scanned = build("01", GTIN, FNC1, "17", "270408", FNC1, "10", "AMX-0021")
        pack = parse(scanned, today=TODAY)
        assert pack.gtin == GTIN
        assert pack.batch_number == "AMX-0021"

    def test_order_does_not_matter(self):
        a = parse(build("01", GTIN, "10", "B1", FNC1, "17", "270408"), today=TODAY)
        b = parse(build("17", "270408", "10", "B1", FNC1, "01", GTIN), today=TODAY)
        assert (a.gtin, a.batch_number, a.expiry_date) == (b.gtin, b.batch_number, b.expiry_date)


class TestScannerQuirks:
    @pytest.mark.parametrize("prefix", ["]d2", "]C1", "]e0", ""])
    def test_symbology_identifier_stripped(self, prefix):
        pack = parse(f"{prefix}01{GTIN}", today=TODAY)
        assert pack.gtin == GTIN

    @pytest.mark.parametrize("marker", ["<GS>", "{GS}", FNC1])
    def test_separator_variants(self, marker):
        """Keyboard-wedge scanners may emit a printable placeholder."""
        pack = parse(build("10", "AMX-0021", marker, "17", "270408"), today=TODAY)
        assert pack.batch_number == "AMX-0021"
        assert pack.expiry_date == date(2027, 4, 8)

    def test_leading_separator_ignored(self):
        assert parse(f"{FNC1}01{GTIN}", today=TODAY).gtin == GTIN

    def test_surrounding_whitespace_ignored(self):
        assert parse(f"  01{GTIN}  ", today=TODAY).gtin == GTIN


class TestRejections:
    def test_empty(self):
        with pytest.raises(GS1ParseError, match="Nothing scanned"):
            parse("", today=TODAY)

    def test_unknown_application_identifier(self):
        with pytest.raises(GS1ParseError, match="Unknown application identifier"):
            parse("99123456", today=TODAY)

    def test_plain_product_barcode_is_not_gs1(self):
        """A bare EAN-13 has no AI and must be rejected, not guessed at."""
        with pytest.raises(GS1ParseError):
            parse("5012345678900", today=TODAY)


class TestUsability:
    def test_gtin_alone_is_usable(self):
        assert parse(f"01{GTIN}", today=TODAY).is_usable is True

    def test_batch_with_expiry_is_usable(self):
        pack = parse(build("10", "AMX-0021", FNC1, "17", "270408"), today=TODAY)
        assert pack.is_usable is True

    def test_serial_alone_is_not(self):
        assert parse(build("21", "SN1"), today=TODAY).is_usable is False
