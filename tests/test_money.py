from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest

from atobenchmark.csvsafe import guard
from atobenchmark.money import AmountError, money, parse_amount, percent, percent_compact


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1000", "1000"),
        ("1,234.56", "1234.56"),
        ("$1,234.56", "1234.56"),
        (" 1234.56 ", "1234.56"),
        ("-500", "-500"),
        ("(500)", "-500"),
        ("($1,500.25)", "-1500.25"),
        ("500 CR", "-500"),
        ("500 cr", "-500"),
        ("500 DR", "500"),
        ("(500) CR", "500"),
        ("0", "0"),
        (".5", "0.5"),
    ],
)
def test_parse_amount_accepts(raw: str, expected: str) -> None:
    assert parse_amount(raw) == Decimal(expected)


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "abc", "1.2.3", "NaN", "nan", "Infinity", "-Infinity", "inf", "1e5", "--5", "5-"],
)
def test_parse_amount_rejects(raw: str) -> None:
    # Decimal happily parses NaN and Infinity and then raises on the first comparison,
    # so they have to be refused at the door.
    with pytest.raises(AmountError):
        parse_amount(raw)


def test_parse_amount_reports_where() -> None:
    with pytest.raises(AmountError) as excinfo:
        parse_amount("oops", "pnl.csv line 4")
    assert "pnl.csv line 4" in str(excinfo.value)


def test_money_formats_with_separators() -> None:
    assert money(Decimal("1234567.891")) == "1,234,567.89"
    assert money(Decimal("-0.005")) == "-0.01"


def test_percent_keeps_two_places() -> None:
    # 30.96% must not print as 31% next to a verdict of "below the 31% to 38% range".
    assert percent(Decimal("0.3096")) == "30.96%"
    assert percent(Decimal("0.31")) == "31.00%"


@pytest.mark.parametrize("formatter", [money, percent, percent_compact])
def test_formatting_a_value_past_the_decimal_context_is_refused(
    formatter: Callable[[Decimal], str],
) -> None:
    # quantize raises InvalidOperation once the result needs more digits than the
    # context holds. That is an ArithmeticError, which the CLI's error handler does
    # not catch, so unconverted it ends the run in a traceback rather than the usual
    # error line.
    with pytest.raises(AmountError):
        formatter(Decimal("1" + "0" * 30))


def test_percent_compact_trims_published_bounds() -> None:
    assert percent_compact(Decimal("0.29")) == "29%"
    assert percent_compact(Decimal("0.295")) == "29.5%"
    assert percent_compact(Decimal("0")) == "0%"


@pytest.mark.parametrize("value", ["=1+1", "@SUM(A1)", "+A1", "-A1", "=cmd|' /c calc'!A0"])
def test_guard_escapes_formula_starts(value: str) -> None:
    assert guard(value).startswith("'")


@pytest.mark.parametrize("value", ["-00123", "+1234.5", "-1,234.56", "Rent", "1234", ""])
def test_guard_leaves_data_alone(value: str) -> None:
    # A ledger code like -00123 has to survive so it still joins back to the ledger.
    assert guard(value) == value


def test_guard_escapes_leading_whitespace_control() -> None:
    assert guard("\t=1+1").startswith("'")
