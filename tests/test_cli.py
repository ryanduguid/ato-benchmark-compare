from __future__ import annotations

import json
from pathlib import Path

import pytest

from atobenchmark.cli import EXIT_ERROR, EXIT_OK, EXIT_OUTSIDE, EXIT_UNREVIEWED, main

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
BAKERY_PNL = EXAMPLES / "bakery-pnl.csv"
BAKERY_MAPPING = EXAMPLES / "bakery-mapping.csv"


def test_industries_lists_every_business_type(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["industries"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Bakeries and hot bread shops" in out
    assert "100 of 100 business types" in out


def test_industries_search(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["industries", "--search", "cleaning"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Cleaning services" in out
    assert "Bakeries" not in out


def test_industries_search_with_no_match(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["industries", "--search", "interstellar freight"]) == EXIT_ERROR
    assert "No business type matches" in capsys.readouterr().err


def test_show(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "bakeries"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "$65,000 - $400,000" in out
    assert "31% to 38%" in out
    assert "Cost of sales to turnover" in out


def test_show_reports_a_ratio_the_ato_does_not_publish(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "Architectural services"]) == EXIT_OK
    assert "not published" in capsys.readouterr().out


def test_buckets(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["buckets"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "associated_persons" in out
    assert "cost_of_sales_labour" in out


def test_worked_example_runs_clean(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "compare",
            "--profit-and-loss", str(BAKERY_PNL),
            "--mapping", str(BAKERY_MAPPING),
            "--industry", "Bakeries and hot bread shops",
        ]
    )
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "Turnover:       $850,000.00 (sales of goods and services)" in out
    assert "Turnover band:  More than $750,000" in out
    assert "Cost of sales to turnover (key)" in out
    assert "31.76%" in out
    assert "83.17%" in out


def test_json_output_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "compare",
            "--profit-and-loss", str(BAKERY_PNL),
            "--mapping", str(BAKERY_MAPPING),
            "--industry", "bakeries",
            "--json", "-",
        ]
    )
    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["business_type"] == "Bakeries and hot bread shops"
    assert payload["key_ratio"] == "cost_of_sales_to_turnover"
    assert payload["turnover"] == "850000.00"
    assert payload["figures"]["total_expenses_for_ratio"] == "706950.00"
    assert payload["source"]["publisher"] == "Australian Taxation Office"
    key = [row for row in payload["ratios"] if row["is_key_ratio"]]
    assert len(key) == 1
    assert key[0]["status"] == "within"
    assert payload["disclaimer"]


def test_json_output_to_a_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "result.json"
    code = main(
        [
            "compare",
            "--profit-and-loss", str(BAKERY_PNL),
            "--mapping", str(BAKERY_MAPPING),
            "--industry", "bakeries",
            "--json", str(out),
        ]
    )
    assert code == EXIT_OK
    assert json.loads(out.read_text(encoding="utf-8"))["benchmark_year"] == "2023-24"
    assert "ATO small business benchmark comparison" in capsys.readouterr().out


def test_map_then_compare_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "m.csv"
    assert main(["map", "--profit-and-loss", str(BAKERY_PNL), "--out", str(out)]) == EXIT_OK
    capsys.readouterr()
    # Nothing has been reviewed yet, so the run reports a comparison and still exits 3.
    code = main(
        [
            "compare",
            "--profit-and-loss", str(BAKERY_PNL),
            "--mapping", str(out),
            "--industry", "bakeries",
        ]
    )
    assert code == EXIT_UNREVIEWED
    assert "Review outstanding" in capsys.readouterr().out


def test_accept_unreviewed_clears_the_review_exit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "m.csv"
    main(["map", "--profit-and-loss", str(BAKERY_PNL), "--out", str(out)])
    capsys.readouterr()
    code = main(
        [
            "compare",
            "--profit-and-loss", str(BAKERY_PNL),
            "--mapping", str(out),
            "--industry", "bakeries",
            "--accept-unreviewed",
        ]
    )
    assert code == EXIT_OK


def test_map_will_not_overwrite_a_reviewed_mapping(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "m.csv"
    assert main(["map", "--profit-and-loss", str(BAKERY_PNL), "--out", str(out)]) == EXIT_OK
    capsys.readouterr()
    assert main(["map", "--profit-and-loss", str(BAKERY_PNL), "--out", str(out)]) == EXIT_ERROR
    assert "--force" in capsys.readouterr().err
    assert main(["map", "--profit-and-loss", str(BAKERY_PNL), "--out", str(out), "--force"]) == EXIT_OK


def test_unmapped_account_blocks_the_comparison(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mapping = tmp_path / "m.csv"
    mapping.write_text("account,bucket\nSales - bread and rolls,turnover\n", encoding="utf-8")
    code = main(
        [
            "compare",
            "--profit-and-loss", str(BAKERY_PNL),
            "--mapping", str(mapping),
            "--industry", "bakeries",
        ]
    )
    assert code == EXIT_ERROR
    err = capsys.readouterr().err
    assert "no mapping entry" in err
    assert "Sales - cakes and pastries" in err


def test_outside_the_key_range_exits_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pnl = tmp_path / "p.csv"
    pnl.write_text(
        "account,amount\nSales,1000000\nPurchases,700000\nRent,50000\n", encoding="utf-8"
    )
    mapping = tmp_path / "m.csv"
    mapping.write_text(
        "account,bucket\nSales,turnover\nPurchases,cost_of_sales\nRent,rent\n", encoding="utf-8"
    )
    code = main(
        [
            "compare",
            "--profit-and-loss", str(pnl),
            "--mapping", str(mapping),
            "--industry", "bakeries",
        ]
    )
    assert code == EXIT_OUTSIDE
    out = capsys.readouterr().out
    assert "70.00%" in out
    assert "above" in out


def test_flip_expense_signs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pnl = tmp_path / "p.csv"
    pnl.write_text("account,amount\nSales,1000000\nPurchases,-320000\n", encoding="utf-8")
    mapping = tmp_path / "m.csv"
    mapping.write_text("account,bucket\nSales,turnover\nPurchases,cost_of_sales\n", encoding="utf-8")
    args = [
        "compare",
        "--profit-and-loss", str(pnl),
        "--mapping", str(mapping),
        "--industry", "bakeries",
    ]
    assert main(args) == EXIT_OUTSIDE
    assert "negative" in capsys.readouterr().out
    assert main(args + ["--flip-expense-signs"]) == EXIT_OK
    assert "32.00%" in capsys.readouterr().out


def test_w1_is_applied_to_the_labour_ratio(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pnl = tmp_path / "p.csv"
    pnl.write_text(
        "account,amount\nSales,1000000\nPurchases,320000\nWages,200000\n", encoding="utf-8"
    )
    mapping = tmp_path / "m.csv"
    mapping.write_text(
        "account,bucket\nSales,turnover\nPurchases,cost_of_sales\nWages,salary_wages\n",
        encoding="utf-8",
    )
    code = main(
        [
            "compare",
            "--profit-and-loss", str(pnl),
            "--mapping", str(mapping),
            "--industry", "bakeries",
            "--w1", "260,000",
            "--json", "-",
        ]
    )
    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["figures"]["labour"] == "260000"
    assert any("W1" in check for check in payload["checks_to_make"])


def test_bad_w1_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "compare",
            "--profit-and-loss", str(BAKERY_PNL),
            "--mapping", str(BAKERY_MAPPING),
            "--industry", "bakeries",
            "--w1", "lots",
        ]
    )
    assert code == EXIT_ERROR
    assert "not an amount" in capsys.readouterr().err


def test_unknown_industry_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "compare",
            "--profit-and-loss", str(BAKERY_PNL),
            "--mapping", str(BAKERY_MAPPING),
            "--industry", "interstellar freight",
        ]
    )
    assert code == EXIT_ERROR
    assert "no ATO business type matches" in capsys.readouterr().err


def test_previous_benchmark_year_can_be_selected(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "compare",
            "--profit-and-loss", str(BAKERY_PNL),
            "--mapping", str(BAKERY_MAPPING),
            "--industry", "bakeries",
            "--year", "2022-23",
        ]
    )
    assert code in {EXIT_OK, EXIT_OUTSIDE}
    assert "Benchmark year: 2022-23" in capsys.readouterr().out
