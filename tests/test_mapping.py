from __future__ import annotations

from pathlib import Path

import pytest

from atobenchmark import mapping
from atobenchmark.mapping import BUCKETS, MappingError, MappingRow, REVIEW


@pytest.mark.parametrize(
    ("account", "section", "expected"),
    [
        ("Sales - bread", None, "turnover"),
        ("Fee income", None, "turnover"),
        ("Accounting fees", "expense", "other_expense"),
        ("Bank fees", "expense", "other_expense"),
        ("Interest received", "income", "other_income"),
        ("Fuel tax credits", None, "other_income"),
        ("Cost of goods sold", None, "cost_of_sales"),
        ("Purchases", None, "cost_of_sales"),
        ("Bakery wages", "cost_of_sales", "cost_of_sales_labour"),
        ("Direct labour", None, "cost_of_sales_labour"),
        ("Shop wages", "expense", "salary_wages"),
        ("Superannuation", "expense", "other_expense"),
        ("Payroll tax", "expense", "other_expense"),
        ("Subcontractor costs", "expense", "contractor_commission"),
        ("Sales commission paid", "expense", "contractor_commission"),
        ("Wages - director spouse", "expense", "associated_persons"),
        ("Management fee - related party", "expense", "associated_persons"),
        ("Rent", "expense", "rent"),
        ("Rental income", "income", "other_income"),
        ("Motor vehicle expenses", "expense", "motor_vehicle"),
        ("Income tax expense", "expense", "excluded"),
    ],
)
def test_suggestions(account: str, section: str | None, expected: str) -> None:
    bucket, _ = mapping.suggest(account, section)
    assert bucket == expected


def test_unmatched_account_without_a_section_needs_review() -> None:
    bucket, reason = mapping.suggest("Sundry", None)
    assert bucket == REVIEW
    assert reason


def test_section_defaults_catch_unmatched_accounts() -> None:
    assert mapping.suggest("Sundry", "expense")[0] == "other_expense"
    assert mapping.suggest("Sundry", "cost_of_sales")[0] == "cost_of_sales"
    assert mapping.suggest("Sundry", "income")[0] == "turnover"


def test_income_wording_in_an_expense_section_is_sent_for_review() -> None:
    # Better to ask than to file income wording as an expense or an expense as income.
    bucket, reason = mapping.suggest("Interest received", "expense")
    assert bucket == REVIEW
    assert "expense section" in reason


def test_every_suggested_bucket_is_a_real_bucket() -> None:
    names = [
        "Sales", "Purchases", "Rent", "Wages", "Superannuation", "Motor vehicle",
        "Interest received", "Income tax expense", "Sundry", "Commission",
    ]
    for name in names:
        for section in (None, "income", "cost_of_sales", "expense"):
            bucket, _ = mapping.suggest(name, section)
            assert bucket == REVIEW or bucket in BUCKETS


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "m.csv"
    mapping.write_mapping(
        path,
        [
            MappingRow(account="Sales", bucket="turnover", source="reviewed", note="", amount="100"),
            MappingRow(account="Rent", bucket="rent", source="suggested", note="rent wording", amount="20"),
        ],
    )
    rows = mapping.read_mapping(path)
    assert set(rows) == {"sales", "rent"}
    assert rows["rent"].source == "suggested"
    assert rows["sales"].bucket == "turnover"


def test_written_account_names_cannot_become_formulas(tmp_path: Path) -> None:
    path = tmp_path / "m.csv"
    mapping.write_mapping(
        path, [MappingRow(account="=cmd|calc", bucket="other_expense", source="reviewed")]
    )
    assert "'=cmd|calc" in path.read_text(encoding="utf-8")


def test_review_marker_blocks_the_run(tmp_path: Path) -> None:
    path = tmp_path / "m.csv"
    path.write_text("account,bucket\nSundry,REVIEW\n", encoding="utf-8")
    with pytest.raises(MappingError) as excinfo:
        mapping.read_mapping(path)
    assert "REVIEW" in str(excinfo.value)


def test_unknown_bucket_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "m.csv"
    path.write_text("account,bucket\nSales,income\n", encoding="utf-8")
    with pytest.raises(MappingError) as excinfo:
        mapping.read_mapping(path)
    assert "unknown bucket" in str(excinfo.value)


def test_empty_bucket_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "m.csv"
    path.write_text("account,bucket\nSales,\n", encoding="utf-8")
    with pytest.raises(MappingError):
        mapping.read_mapping(path)


def test_duplicate_account_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "m.csv"
    path.write_text("account,bucket\nSales,turnover\nSALES,turnover\n", encoding="utf-8")
    with pytest.raises(MappingError) as excinfo:
        mapping.read_mapping(path)
    assert "more than once" in str(excinfo.value)


def test_missing_column_names_what_is_missing(tmp_path: Path) -> None:
    path = tmp_path / "m.csv"
    path.write_text("account,category\nSales,turnover\n", encoding="utf-8")
    with pytest.raises(MappingError) as excinfo:
        mapping.read_mapping(path)
    assert "bucket" in str(excinfo.value)


def test_byte_order_mark_and_odd_case_headers_are_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "m.csv"
    path.write_bytes("Account,Bucket\nSales,turnover\n".encode("utf-8-sig"))
    rows = mapping.read_mapping(path)
    assert rows["sales"].bucket == "turnover"


def test_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(MappingError):
        mapping.read_mapping(tmp_path / "nope.csv")


def test_account_key_ignores_case_and_spacing() -> None:
    assert mapping.normalise_account("  Motor   Vehicle ") == mapping.normalise_account("motor vehicle")
