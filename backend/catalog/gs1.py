"""GS1 DataMatrix parsing.

A pharmaceutical pack carries a 2D barcode encoding GTIN, expiry, batch
and — where serialized — a serial. Parsing it means a scan at receiving
auto-fills batch and expiry, and a scan at point of sale resolves the
exact batch. That single capability removes most manual data entry in the
system.

See docs/06-compliance.md §10 and docs/13-research.md finding 10.

Two details that quietly cause wrong data if missed:

1. Variable-length elements are terminated by FNC1, which scanners emit
   as GS (0x1D). Without honouring it, a batch number swallows whatever
   follows it.
2. In a YYMMDD date, **DD = 00 means the last day of that month**. Read
   naively it becomes day zero, and either raises or silently shifts to
   the previous month — an expiry error in the direction that lets
   expired stock be sold.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

FNC1 = "\x1d"

# Symbology identifiers a scanner may prefix. ]d2 is DataMatrix with FNC1
# in the first position; ]C1 is GS1-128; ]e0 is GS1 DataBar.
SYMBOLOGY_PREFIX = re.compile(r"^\](?:d2|C1|e0|Q3|d1)")

#: Application identifiers we understand, with their fixed data length.
#: ``None`` means variable length, terminated by FNC1 or end of string.
FIXED_LENGTH: dict[str, int | None] = {
    "00": 18,  # SSCC
    "01": 14,  # GTIN
    "11": 6,   # production date
    "15": 6,   # best before
    "17": 6,   # expiry
    "10": None,  # batch / lot
    "21": None,  # serial
    "240": None,  # additional product identification
    "30": None,  # variable count
}

MAX_VARIABLE = 20


class GS1ParseError(ValueError):
    """The scanned string is not a well-formed GS1 element string."""


@dataclass(frozen=True, slots=True)
class ScannedPack:
    """What a scan tells us about a physical pack."""

    gtin: str | None = None
    batch_number: str | None = None
    expiry_date: date | None = None
    production_date: date | None = None
    best_before: date | None = None
    serial: str | None = None
    raw: str = ""

    @property
    def is_usable(self) -> bool:
        """Enough to identify a pack: a GTIN, or a batch with an expiry."""
        return bool(self.gtin) or bool(self.batch_number and self.expiry_date)


def parse(scanned: str, *, today: date | None = None) -> ScannedPack:
    """Parse a GS1 element string into its parts.

    Accepts the raw string a scanner emits, with or without a symbology
    identifier, and with FNC1 as either the GS control character or the
    literal sequence ``<GS>``.
    """
    if not scanned or not scanned.strip():
        raise GS1ParseError("Nothing scanned.")

    raw = scanned
    data = scanned.strip()
    data = SYMBOLOGY_PREFIX.sub("", data)
    # Some keyboard-wedge scanners emit a printable placeholder instead of
    # the control character.
    data = data.replace("<GS>", FNC1).replace("{GS}", FNC1)
    data = data.lstrip(FNC1)

    fields: dict[str, str] = {}
    index = 0

    while index < len(data):
        ai, length = _read_ai(data, index)
        index += len(ai)

        if length is None:
            end = data.find(FNC1, index)
            if end == -1:
                end = len(data)
            value = data[index:end]
            index = end + 1 if end < len(data) else end
            if len(value) > MAX_VARIABLE:
                raise GS1ParseError(
                    f"AI ({ai}) value is {len(value)} characters; maximum is {MAX_VARIABLE}."
                )
        else:
            value = data[index : index + length]
            if len(value) < length:
                raise GS1ParseError(
                    f"AI ({ai}) needs {length} characters, found {len(value)}."
                )
            index += length
            # A fixed-length element may still be followed by a separator.
            if index < len(data) and data[index] == FNC1:
                index += 1

        if not value:
            raise GS1ParseError(f"AI ({ai}) has no value.")
        fields[ai] = value

    return ScannedPack(
        gtin=_normalize_gtin(fields.get("01")),
        batch_number=fields.get("10"),
        expiry_date=_parse_date(fields.get("17"), today=today),
        production_date=_parse_date(fields.get("11"), today=today),
        best_before=_parse_date(fields.get("15"), today=today),
        serial=fields.get("21"),
        raw=raw,
    )


def _read_ai(data: str, index: int) -> tuple[str, int | None]:
    """Application identifiers are 2–4 digits; match the longest known."""
    for size in (4, 3, 2):
        candidate = data[index : index + size]
        if candidate in FIXED_LENGTH:
            return candidate, FIXED_LENGTH[candidate]
    raise GS1ParseError(f"Unknown application identifier at position {index}: {data[index:index + 4]!r}")


def _normalize_gtin(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.isdigit():
        raise GS1ParseError("GTIN must be numeric.")
    if not _check_digit_valid(value):
        raise GS1ParseError(f"GTIN {value} fails its check digit.")
    return value


def _check_digit_valid(gtin: str) -> bool:
    """GS1 modulo-10 check digit.

    Weights alternate 3 and 1 from the right, excluding the check digit
    itself.
    """
    digits = [int(c) for c in gtin]
    body, check = digits[:-1], digits[-1]
    total = 0
    for position, digit in enumerate(reversed(body)):
        total += digit * (3 if position % 2 == 0 else 1)
    return (10 - total % 10) % 10 == check


def _parse_date(value: str | None, *, today: date | None = None) -> date | None:
    """YYMMDD, where DD = 00 means the last day of the month.

    The two-digit year resolves to the century that places it within
    roughly fifty years of today, per the GS1 General Specifications.
    """
    if value is None:
        return None
    if len(value) != 6 or not value.isdigit():
        raise GS1ParseError(f"Date {value!r} is not YYMMDD.")

    today = today or date.today()
    yy, mm, dd = int(value[:2]), int(value[2:4]), int(value[4:6])

    if not 1 <= mm <= 12:
        raise GS1ParseError(f"Month {mm:02d} is not valid.")

    year = _resolve_year(yy, today.year)

    if dd == 0:
        # "No specific day" — the element refers to the end of the month.
        dd = calendar.monthrange(year, mm)[1]
    elif dd > calendar.monthrange(year, mm)[1]:
        raise GS1ParseError(f"Day {dd:02d} is not valid for {year}-{mm:02d}.")

    return date(year, mm, dd)


def _resolve_year(two_digit: int, current_year: int) -> int:
    """Pick the century that puts the year nearest to now.

    A pack expiring in '27 is 2027 today, not 1927 or 2127.
    """
    century = current_year - current_year % 100
    candidates = (century - 100 + two_digit, century + two_digit, century + 100 + two_digit)
    return min(candidates, key=lambda year: abs(year - current_year))
