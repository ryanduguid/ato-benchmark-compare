from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from atobenchmark import pnl


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


NEUTRAL = """account,amount
Sales,850000
Purchases,290000
Rent,60000
"""

REPORT = """Demo Pty Ltd,,
Profit and Loss,,
,,
Account,30 Jun 2024,30 Jun 2023
Income,,
Sales,850000.00,800000.00
Total Income,850000.00,800000.00
Less Cost of Sales,,
Purchases,290000.00,270000.00
Total Cost of Sales,290000.00,270000.00
Gross Profit,560000.00,530000.00
Less Operating Expenses,,
Rent,60000.00,58000.00
Total Operating Expenses,60000.00,58000.00
Net Profit,500000.00,472000.00
"""


def test_neutral_layout(tmp_path: Path) -> None:
    result = pnl.read(write(tmp_path, "p.csv", NEUTRAL))
    assert result.layout == "neutral"
    assert [row.account for row in result.rows] == ["Sales", "Purchases", "Rent"]
    assert result.rows[0].amount == Decimal("850000")


def test_neutral_layout_accepts_a_section_column(tmp_path: Path) -> None:
    text = "account,amount,section\nSales,100,income\nPurchases,40,cost_of_sales\n"
    result = pnl.read(write(tmp_path, "p.csv", text))
    assert [row.section for row in result.rows] == ["income", "cost_of_sales"]


def test_neutral_layout_refuses_an_unknown_section(tmp_path: Path) -> None:
    text = "account,amount,section\nSales,100,revenue\n"
    with pytest.raises(pnl.PnlError):
        pnl.read(write(tmp_path, "p.csv", text))


def test_misaligned_row_is_an_error_not_a_silent_shift(tmp_path: Path) -> None:
    # An unquoted comma inside an account name pushes the amount into another column.
    text = "account,amount\nSales, Hire,850000\n"
    with pytest.raises(pnl.PnlError) as excinfo:
        pnl.read(write(tmp_path, "p.csv", text))
    assert "unquoted comma" in str(excinfo.value)


def test_trailing_blank_cells_are_tolerated(tmp_path: Path) -> None:
    result = pnl.read(write(tmp_path, "p.csv", "account,amount\nSales,100,\n"))
    assert result.rows[0].amount == Decimal("100")


def test_row_missing_its_amount_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(pnl.PnlError) as excinfo:
        pnl.read(write(tmp_path, "p.csv", "account,amount\nRent\n"))
    assert "no amount given" in str(excinfo.value)


def test_unreadable_amount_in_neutral_layout_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(pnl.PnlError):
        pnl.read(write(tmp_path, "p.csv", "account,amount\nSales,not a number\n"))


def test_report_layout_detects_sections_and_totals(tmp_path: Path) -> None:
    result = pnl.read(write(tmp_path, "p.csv", REPORT))
    assert result.layout == "report"
    sections = {row.account: row.section for row in result.rows}
    assert sections["Sales"] == pnl.SECTION_INCOME
    assert sections["Purchases"] == pnl.SECTION_COST_OF_SALES
    assert sections["Rent"] == pnl.SECTION_EXPENSE
    totals = [row.account for row in result.totals]
    assert totals == ["Total Income", "Total Cost of Sales", "Gross Profit", "Total Operating Expenses", "Net Profit"]
    assert [row.account for row in result.accounts] == ["Sales", "Purchases", "Rent"]


def test_report_layout_picks_the_first_value_column(tmp_path: Path) -> None:
    result = pnl.read(write(tmp_path, "p.csv", REPORT))
    assert result.amount_column == "column 1"
    assert next(row for row in result.rows if row.account == "Sales").amount == Decimal("850000.00")


def test_amount_column_can_be_chosen_by_number(tmp_path: Path) -> None:
    result = pnl.read(write(tmp_path, "p.csv", REPORT), amount_column="2")
    assert next(row for row in result.rows if row.account == "Sales").amount == Decimal("800000.00")


def test_amount_column_can_be_chosen_by_heading(tmp_path: Path) -> None:
    result = pnl.read(write(tmp_path, "p.csv", REPORT), amount_column="30 Jun 2023")
    assert result.amount_column == "30 Jun 2023"
    assert next(row for row in result.rows if row.account == "Sales").amount == Decimal("800000.00")


def test_unknown_amount_column_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(pnl.PnlError):
        pnl.read(write(tmp_path, "p.csv", REPORT), amount_column="30 Jun 2019")
    with pytest.raises(pnl.PnlError):
        pnl.read(write(tmp_path, "p.csv", REPORT), amount_column="9")


def test_report_layout_records_what_it_skipped(tmp_path: Path) -> None:
    result = pnl.read(write(tmp_path, "p.csv", REPORT))
    joined = " ".join(result.skipped)
    assert "Demo Pty Ltd" in joined
    assert "Profit and Loss" in joined


def test_empty_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(pnl.PnlError):
        pnl.read(write(tmp_path, "p.csv", "   \n"))


def test_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(pnl.PnlError):
        pnl.read(tmp_path / "nope.csv")


def test_byte_order_mark_is_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "p.csv"
    path.write_bytes(b"\xef\xbb\xbf" + NEUTRAL.encode("utf-8"))
    assert pnl.read(path).layout == "neutral"


@pytest.mark.parametrize(
    "label",
    ["Total Income", "total cost of sales", "Gross Profit", "Net Loss", "Profit before income tax", "Earnings before interest"],
)
def test_total_rows_are_recognised(label: str) -> None:
    assert pnl.is_total_row(label)


@pytest.mark.parametrize("label", ["Totalisator fees", "Rent", "Sales", "Netting expenses"])
def test_ordinary_accounts_are_not_mistaken_for_totals(label: str) -> None:
    assert not pnl.is_total_row(label)


@pytest.mark.parametrize(
    ("label", "section"),
    [
        ("Income", pnl.SECTION_INCOME),
        ("Trading Income", pnl.SECTION_INCOME),
        ("Other Income", pnl.SECTION_INCOME),
        ("Less Cost of Sales", pnl.SECTION_COST_OF_SALES),
        ("Cost of Goods Sold", pnl.SECTION_COST_OF_SALES),
        ("Less Operating Expenses", pnl.SECTION_EXPENSE),
        ("Expenses", pnl.SECTION_EXPENSE),
        ("Overheads", pnl.SECTION_EXPENSE),
    ],
)
def test_section_headings(label: str, section: str) -> None:
    assert pnl.section_for(label) == section


def test_a_named_account_is_not_treated_as_a_section() -> None:
    assert pnl.section_for("Rent") is None
    assert pnl.section_for("Income protection insurance") is None
