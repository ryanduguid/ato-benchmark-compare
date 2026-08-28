from __future__ import annotations

import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from atobenchmark import mapping as mapping_module
from atobenchmark.cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_OUTSIDE,
    EXIT_UNREVIEWED,
    main,
)
from atobenchmark.mapping import MappingError, MappingRow, read_mapping
from atobenchmark.pnl import PnlRow

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
BAKERY_PNL = EXAMPLES / "bakery-pnl.csv"
BAKERY_MAPPING = EXAMPLES / "bakery-mapping.csv"


def _digest(normalised_account: str) -> str:
    return hashlib.sha256(normalised_account.encode("utf-8")).hexdigest()


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


def test_map_does_not_silently_bucket_cost_of_sales_as_turnover(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A placeholder amount under "Cost of Sales" makes the section-total check
    # inconclusive. The heading must still advance the section: its amount appears in
    # the mapping file as a suggested exclusion, the rows beneath it are suggested as
    # cost of sales rather than turnover, and the unreadable row is reported.
    pnl = tmp_path / "p.csv"
    pnl.write_text(
        "Income,850000\nSales,850000\nCost of Sales,290000\n"
        "Purchases,240000\nFreight inwards,-\nFreight inwards (2),50000\n",
        encoding="utf-8",
    )
    out = tmp_path / "m.csv"
    assert main(["map", "--profit-and-loss", str(pnl), "--out", str(out)]) == EXIT_OK
    printed = capsys.readouterr().out
    assert "line 5: 'Freight inwards' has no readable amount" in printed
    buckets = {row.account: row.bucket for row in read_mapping(out).values()}
    assert buckets["Cost of Sales"] == "excluded"
    assert buckets["Purchases"] == "cost_of_sales"
    assert buckets["Freight inwards (2)"] == "cost_of_sales"
    assert "turnover" not in {buckets["Purchases"], buckets["Freight inwards (2)"]}
    # The excluded heading keeps its amount visible for the reviewer.
    assert "290000" in out.read_text(encoding="utf-8")


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


def test_map_then_compare_keeps_guarded_and_apostrophe_accounts_distinct(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pnl = tmp_path / "source.csv"
    pnl.write_text(
        "account,amount\n=cmd|calc,1000000\n'=cmd|calc,320000\n", encoding="utf-8"
    )
    mapped = tmp_path / "mapped.csv"
    assert main(["map", "--profit-and-loss", str(pnl), "--out", str(mapped)]) == EXIT_OK
    capsys.readouterr()

    with mapped.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    header = rows[0]
    account_key_at = header.index("account_key")
    bucket_at = header.index("bucket")
    source_at = header.index("source")
    expected_buckets = {
        _digest("=cmd|calc"): "turnover",
        _digest("'=cmd|calc"): "cost_of_sales",
    }
    for row in rows[1:]:
        row[bucket_at] = expected_buckets[row[account_key_at]]
        row[source_at] = "reviewed"
    with mapped.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)

    code = main(
        [
            "compare",
            "--profit-and-loss",
            str(pnl),
            "--mapping",
            str(mapped),
            "--industry",
            "bakeries",
            "--json",
            "-",
        ]
    )
    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["bucket_totals"]["turnover"] == "1000000"
    assert payload["bucket_totals"]["cost_of_sales"] == "320000"
    assert payload["figures"]["cost_of_sales_for_ratio"] == "320000"


def test_map_fails_when_distinct_accounts_share_a_forced_digest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    pnl = tmp_path / "source.csv"
    pnl.write_text("account,amount\nSales,100\nRent,10\n", encoding="utf-8")
    monkeypatch.setattr(mapping_module, "account_key", lambda _account: "a" * 64, raising=False)
    code = main(["map", "--profit-and-loss", str(pnl), "--out", str(tmp_path / "m.csv")])
    assert code == EXIT_ERROR
    assert "collision" in capsys.readouterr().err.casefold()


def test_map_still_collapses_normalisation_equivalent_accounts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pnl = tmp_path / "source.csv"
    pnl.write_text(
        "account,amount\nSales Account,100\nSALES   ACCOUNT,200\n", encoding="utf-8"
    )
    mapped = tmp_path / "m.csv"
    assert main(["map", "--profit-and-loss", str(pnl), "--out", str(mapped)]) == EXIT_OK
    assert "collapsed to one row" in capsys.readouterr().out
    assert len(read_mapping(mapped)) == 1


def test_comparison_rejects_a_forced_digest_collision_before_routing_amounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mapping_module, "account_key", lambda _account: "a" * 64, raising=False)
    source = (PnlRow("Rent", Decimal("10"), line_number=2),)
    rows = {"a" * 64: MappingRow("Sales", "turnover", "reviewed")}
    with pytest.raises(MappingError, match="collision"):
        mapping_module.route(source, rows, flip=False)
