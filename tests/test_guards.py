"""Tests for the ways this tool can quietly produce a wrong or destructive answer."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from atobenchmark import pnl
from atobenchmark.cli import EXIT_ERROR, EXIT_OK, main
from atobenchmark.money import money

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
BAKERY_PNL = EXAMPLES / "bakery-pnl.csv"
BAKERY_MAPPING = EXAMPLES / "bakery-mapping.csv"


def test_section_heading_carrying_its_own_total_is_not_an_account(tmp_path: Path) -> None:
    # Some exports print the section total on the heading row. Treated as an account it
    # would double count everything underneath it. This is a report layout rule: a two
    # column file has no section headings, only the accounts the user chose to list.
    text = "Demo Pty Ltd,\nIncome,850000\nSales,850000\nLess Operating Expenses,60000\nRent,60000\n"
    path = tmp_path / "p.csv"
    path.write_text(text, encoding="utf-8")
    result = pnl.read(path)
    by_name = {row.account: row for row in result.rows}
    assert by_name["Income"].is_total is True
    assert by_name["Less Operating Expenses"].is_total is True
    assert [row.account for row in result.accounts] == ["Sales", "Rent"]


def test_quoted_newline_in_an_account_name_does_not_split_the_row(tmp_path: Path) -> None:
    path = tmp_path / "p.csv"
    path.write_text('account,amount\n"Sales\nand hire",850000\nRent,1000\n', encoding="utf-8")
    result = pnl.read(path)
    assert len(result.rows) == 2
    assert result.rows[0].amount == Decimal("850000")


def test_map_refuses_to_write_over_its_own_input(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    pnl_path = tmp_path / "p.csv"
    pnl_path.write_text("account,amount\nSales,100\n", encoding="utf-8")
    code = main(["map", "--profit-and-loss", str(pnl_path), "--out", str(pnl_path), "--force"])
    assert code == EXIT_ERROR
    assert "reads that same file" in capsys.readouterr().err
    # The input is untouched.
    assert pnl_path.read_text(encoding="utf-8") == "account,amount\nSales,100\n"


def test_compare_refuses_to_write_json_over_the_mapping(capsys: pytest.CaptureFixture[str]) -> None:
    before = BAKERY_MAPPING.read_text(encoding="utf-8")
    code = main(
        [
            "compare",
            "--profit-and-loss", str(BAKERY_PNL),
            "--mapping", str(BAKERY_MAPPING),
            "--industry", "bakeries",
            "--json", str(BAKERY_MAPPING),
        ]
    )
    assert code == EXIT_ERROR
    assert "reads that same file" in capsys.readouterr().err
    assert BAKERY_MAPPING.read_text(encoding="utf-8") == before


def test_repeated_account_names_block_the_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # One mapping row cannot answer for two ledger rows with the same name.
    pnl_path = tmp_path / "p.csv"
    pnl_path.write_text(
        "account,amount\nSales,500000\nOther,1000\nOTHER,2000\n", encoding="utf-8"
    )
    mapping_path = tmp_path / "m.csv"
    mapping_path.write_text(
        "account,bucket\nSales,turnover\nOther,other_expense\n", encoding="utf-8"
    )
    code = main(
        [
            "compare",
            "--profit-and-loss", str(pnl_path),
            "--mapping", str(mapping_path),
            "--industry", "bakeries",
        ]
    )
    assert code == EXIT_ERROR
    assert "more than once" in capsys.readouterr().err


def test_mapping_rows_that_match_nothing_are_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pnl_path = tmp_path / "p.csv"
    # 175,000 over 500,000 is 35%, inside the 34% to 39% medium band for bakeries, so
    # this test is about the unused mapping row and not about the verdict.
    pnl_path.write_text("account,amount\nSales,500000\nPurchases,175000\n", encoding="utf-8")
    mapping_path = tmp_path / "m.csv"
    mapping_path.write_text(
        "account,bucket\nSales,turnover\nPurchases,cost_of_sales\nStale account,rent\n",
        encoding="utf-8",
    )
    assert main(
        [
            "compare",
            "--profit-and-loss", str(pnl_path),
            "--mapping", str(mapping_path),
            "--industry", "bakeries",
        ]
    ) == EXIT_OK
    assert "did not match any account" in capsys.readouterr().out


def test_subtotal_mapped_to_a_real_bucket_is_called_out(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pnl_path = tmp_path / "p.csv"
    pnl_path.write_text(
        "account,amount\nSales,500000\nPurchases,160000\nTotal Cost of Sales,160000\n",
        encoding="utf-8",
    )
    mapping_path = tmp_path / "m.csv"
    mapping_path.write_text(
        "account,bucket\nSales,turnover\nPurchases,cost_of_sales\nTotal Cost of Sales,cost_of_sales\n",
        encoding="utf-8",
    )
    main(
        [
            "compare",
            "--profit-and-loss", str(pnl_path),
            "--mapping", str(mapping_path),
            "--industry", "bakeries",
        ]
    )
    assert "double counting" in capsys.readouterr().out


def test_money_never_prints_negative_zero() -> None:
    assert money(Decimal("-0.001")) == "0.00"
    assert money(Decimal("-0")) == "0.00"


def test_no_runtime_module_imports_anything_outside_the_standard_library() -> None:
    # The runtime has no dependencies. A stray import would break the promise that
    # nothing is fetched and nothing is installed alongside it. Read the import
    # statements with ast: scanning source lines for "from " also matches prose in a
    # docstring, which makes the test fail on sentences and pass on nothing useful.
    import ast

    import atobenchmark

    package = Path(atobenchmark.__file__).parent
    allowed = {
        "__future__", "argparse", "collections", "contextlib", "csv", "hashlib", "io", "json",
        "os", "re", "sys", "tempfile", "typing", "unicodedata", "dataclasses",
        "decimal", "pathlib",
    }
    found = set()
    for source in sorted(package.glob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add((source.name, alias.name.split(".")[0]))
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # a relative import, so within this package
                    continue
                found.add((source.name, (node.module or "").split(".")[0]))
    outside = {(name, module) for name, module in found if module not in allowed}
    assert not outside, f"imports outside the standard library: {sorted(outside)}"
    # The scanner has to actually see the imports, or it proves nothing.
    assert ("dataset.py", "json") in found


def test_amount_column_is_refused_where_it_cannot_apply(tmp_path: Path) -> None:
    # Accepting the option silently would leave the user believing they had selected a
    # comparative period that a two column file does not have.
    path = tmp_path / "p.csv"
    path.write_text("account,amount\nSales,100\n", encoding="utf-8")
    with pytest.raises(pnl.PnlError) as excinfo:
        pnl.read(path, amount_column="2")
    assert "--amount-column does not apply" in str(excinfo.value)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1,000 CR", "-1000"), ("1,000 DR", "1000"), ("CR", None), ("DR", None)],
)
def test_trailing_credit_marker(raw: str, expected: str | None) -> None:
    from atobenchmark.money import AmountError, parse_amount

    if expected is None:
        with pytest.raises(AmountError):
            parse_amount(raw)
    else:
        assert parse_amount(raw) == Decimal(expected)
