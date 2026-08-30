"""Amount parsing and formatting.

Everything downstream works in Decimal. No amount is ever converted to float, so a
ratio of 0.31 compares equal to the ATO's published 0.31 rather than to a binary
approximation of it.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENTS = Decimal("0.01")
PERCENT_PLACES = Decimal("0.01")

_STRIP_RE = re.compile(r"[\s$,]")
_TRAILING_SIGNS = ("CR", "DR")


class AmountError(ValueError):
    """Raised when a cell cannot be read as an amount."""


def parse_amount(raw: str, where: str = "amount") -> Decimal:
    """Parse an accounting amount.

    Accepts a plain number, thousands separators, a leading currency symbol,
    parentheses for negatives and a trailing CR marker. Rejects anything else,
    including the strings Decimal itself would happily accept such as "NaN" and
    "Infinity", which parse cleanly and then explode on the first comparison.
    """
    if raw is None:
        raise AmountError(f"{where}: no amount given")
    text = str(raw).strip()
    if not text:
        raise AmountError(f"{where}: no amount given")

    negative = False
    # A plain suffix test rather than a regex with a lazy prefix, which would scan the
    # whole cell again for every starting position when the suffix is not there.
    suffix = text[-2:].upper()
    if len(text) > 2 and suffix in _TRAILING_SIGNS:
        negative = suffix == "CR"
        text = text[:-2].strip()

    if text.startswith("(") and text.endswith(")"):
        negative = not negative
        text = text[1:-1].strip()

    cleaned = _STRIP_RE.sub("", text)
    if not cleaned:
        raise AmountError(f"{where}: no amount given")
    if not re.fullmatch(r"[+-]?\d*\.?\d+", cleaned):
        raise AmountError(f"{where}: {raw!r} is not an amount")

    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise AmountError(f"{where}: {raw!r} is not an amount") from exc
    if not value.is_finite():
        raise AmountError(f"{where}: {raw!r} is not a finite amount")
    return -value if negative else value


def _quantise(value: Decimal, places: Decimal) -> Decimal:
    """Round to fixed places, refusing a value the decimal context cannot hold.

    quantize raises InvalidOperation once the result would need more digits than the
    context allows. That is an ArithmeticError, so the CLI's error handler does not
    see it and left alone it ends a run with a traceback after the comparison has
    already been produced. AmountError is a ValueError the handler already catches.
    """
    try:
        return value.quantize(places, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise AmountError(f"{value} has more digits than this tool can report") from exc


def money(value: Decimal) -> str:
    """Format an amount for display, with thousands separators and two decimals."""
    quantised = _quantise(value, CENTS)
    if quantised == 0:
        # Decimal keeps the sign through quantize, so -0.001 would print as -0.00.
        quantised = abs(quantised)
    return f"{quantised:,.2f}"


def percent(value: Decimal) -> str:
    """Format a ratio as a percentage of turnover.

    Two decimal places, because the ATO publishes benchmarks to whole percentages and
    a computed 30.96% must not be displayed as 31% next to a verdict of "below the
    31% to 38% range".
    """
    scaled = _quantise(value * 100, PERCENT_PLACES)
    return f"{scaled}%"


def percent_compact(value: Decimal) -> str:
    """Format a published benchmark bound without padding zeros: 29% rather than 29.00%."""
    scaled = _quantise(value * 100, PERCENT_PLACES)
    text = f"{scaled:.2f}".rstrip("0").rstrip(".")
    return f"{text or '0'}%"


def percent_range(low: Decimal, high: Decimal) -> str:
    return f"{percent_compact(low)} to {percent_compact(high)}"
