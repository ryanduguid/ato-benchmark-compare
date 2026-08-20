from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

from atobenchmark import mapping
from atobenchmark.mapping import BUCKETS, MappingError, MappingRow, REVIEW


def _digest(normalised_account: str) -> str:
    """Derive an expected key without calling the production identity helper."""
    return hashlib.sha256(normalised_account.encode("utf-8")).hexdigest()


def _write_keyed_rows(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("account", "account_key", "bucket", "source", "amount", "note"))
        writer.writerows(rows)


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
    assert set(rows) == {_digest("sales"), _digest("rent")}
    assert rows[_digest("rent")].source == "suggested"
    assert rows[_digest("sales")].bucket == "turnover"


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
    assert rows[_digest("sales")].bucket == "turnover"


def test_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(MappingError):
        mapping.read_mapping(tmp_path / "nope.csv")


def test_account_key_ignores_case_and_spacing() -> None:
    assert mapping.normalise_account("  Motor   Vehicle ") == mapping.normalise_account("motor vehicle")


@pytest.mark.parametrize(
    ("account", "displayed", "normalised"),
    [
        ("=cmd|calc", "'=cmd|calc", "=cmd|calc"),
        ("@SUM(A1:A2)", "'@SUM(A1:A2)", "@sum(a1:a2)"),
        ("+A1", "'+A1", "+a1"),
        ("-A1", "'-A1", "-a1"),
        ("\t=cmd", "'\t=cmd", "=cmd"),
        ("\r=cmd", "'\r=cmd", "=cmd"),
        ("\n=cmd", "'\n=cmd", "=cmd"),
        ("+00123", "+00123", "+00123"),
        ("-00123", "-00123", "-00123"),
    ],
)
def test_keyed_round_trip_preserves_formula_guarding_and_logical_identity(
    tmp_path: Path, account: str, displayed: str, normalised: str
) -> None:
    path = tmp_path / "m.csv"
    mapping.write_mapping(
        path,
        [MappingRow(account=account, bucket="other_expense", source="reviewed", amount="1")],
    )

    with path.open("r", encoding="utf-8", newline="") as handle:
        physical = list(csv.reader(handle))
    expected_key = _digest(normalised)
    assert physical[0] == ["account", "account_key", "bucket", "source", "amount", "note"]
    assert physical[1][0] == displayed
    assert physical[1][1] == expected_key
    if account == "=cmd|calc":
        assert expected_key == "a7ccfdf05b929a4f73e80be5998dd4e49243a2228e283a5b75f59f9bf7524a59"

    rows = mapping.read_mapping(path)
    assert rows[expected_key].account == account


def test_formula_and_genuine_apostrophe_accounts_keep_distinct_keys(tmp_path: Path) -> None:
    path = tmp_path / "m.csv"
    mapping.write_mapping(
        path,
        [
            MappingRow(account="=cmd|calc", bucket="turnover", source="reviewed", amount="100"),
            MappingRow(
                account="'=cmd|calc", bucket="cost_of_sales", source="reviewed", amount="32"
            ),
        ],
    )

    with path.open("r", encoding="utf-8", newline="") as handle:
        physical = list(csv.reader(handle))
    formula_key = _digest("=cmd|calc")
    apostrophe_key = _digest("'=cmd|calc")
    assert [row[0] for row in physical[1:]] == ["'=cmd|calc", "'=cmd|calc"]
    assert [row[1] for row in physical[1:]] == [formula_key, apostrophe_key]

    rows = mapping.read_mapping(path)
    assert rows[formula_key].account == "=cmd|calc"
    assert rows[formula_key].bucket == "turnover"
    assert rows[apostrophe_key].account == "'=cmd|calc"
    assert rows[apostrophe_key].bucket == "cost_of_sales"


def test_read_then_write_does_not_change_guarded_account_identity(tmp_path: Path) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    original = [
        MappingRow(
            account="=cmd|calc",
            bucket="other_expense",
            source="reviewed",
            amount="10",
            note="checked",
        ),
        MappingRow(
            account="'=cmd|calc",
            bucket="rent",
            source="reviewed",
            amount="20",
            note="checked separately",
        ),
    ]
    mapping.write_mapping(first, original)
    mapping.write_mapping(second, list(mapping.read_mapping(first).values()))
    assert second.read_bytes() == first.read_bytes()


@pytest.mark.parametrize(
    ("displayed", "supplied_key"),
    [
        ("Renamed account", _digest("sales")),
        ("=cmd|calc", _digest("=cmd|calc")),
        ("'=cmd|calc", _digest("rent")),
    ],
)
def test_keyed_mapping_rejects_display_that_does_not_match_the_key(
    tmp_path: Path, displayed: str, supplied_key: str
) -> None:
    path = tmp_path / "m.csv"
    _write_keyed_rows(
        path,
        [[displayed, supplied_key, "other_expense", "reviewed", "1", ""]],
    )
    with pytest.raises(MappingError, match="identity|guard|regenerate"):
        mapping.read_mapping(path)


@pytest.mark.parametrize(
    "bad_key",
    [
        "",
        " " + _digest("sales"),
        _digest("sales") + " ",
        _digest("sales").upper(),
        "a" * 63,
        "a" * 65,
        "g" * 64,
    ],
)
def test_keyed_mapping_rejects_blank_or_noncanonical_key(tmp_path: Path, bad_key: str) -> None:
    path = tmp_path / "m.csv"
    _write_keyed_rows(path, [["Sales", bad_key, "turnover", "reviewed", "1", ""]])
    with pytest.raises(MappingError, match="account_key"):
        mapping.read_mapping(path)


def test_keyed_file_does_not_fall_back_to_legacy_for_a_blank_row_key(tmp_path: Path) -> None:
    path = tmp_path / "m.csv"
    _write_keyed_rows(
        path,
        [
            ["Sales", _digest("sales"), "turnover", "reviewed", "100", ""],
            ["Rent", "", "rent", "reviewed", "10", ""],
        ],
    )
    with pytest.raises(MappingError, match="account_key"):
        mapping.read_mapping(path)


@pytest.mark.parametrize("keyed", [True, False], ids=["keyed", "legacy"])
@pytest.mark.parametrize("account", ["", " \t\r\n "], ids=["empty", "normalises-empty"])
def test_populated_mapping_row_with_empty_logical_account_is_rejected(
    tmp_path: Path, keyed: bool, account: str
) -> None:
    path = tmp_path / "m.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if keyed:
            writer.writerow(("account", "account_key", "bucket", "source"))
            writer.writerow(("Sales", _digest("sales"), "turnover", "reviewed"))
            writer.writerow((account, _digest("rent"), "rent", "reviewed"))
        else:
            writer.writerow(("account", "bucket", "source"))
            writer.writerow(("Sales", "turnover", "reviewed"))
            writer.writerow((account, "rent", "reviewed"))

    with pytest.raises(MappingError, match="account.*empty|missing.*account"):
        mapping.read_mapping(path)


@pytest.mark.parametrize("account", ["", " \t\r\n "], ids=["empty", "normalises-empty"])
def test_write_rejects_empty_logical_account_before_replacing_existing_file(
    tmp_path: Path, account: str
) -> None:
    path = tmp_path / "m.csv"
    original = b"existing reviewed mapping\r\n"
    path.write_bytes(original)

    with pytest.raises(MappingError, match="account.*empty"):
        mapping.write_mapping(
            path,
            [MappingRow(account=account, bucket="rent", source="reviewed")],
        )

    assert path.read_bytes() == original


def test_case_and_spacing_only_keyed_display_edit_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "m.csv"
    _write_keyed_rows(
        path,
        [["  SALES   ACCOUNT  ", _digest("sales account"), "turnover", "reviewed", "1", ""]],
    )
    rows = mapping.read_mapping(path)
    assert rows[_digest("sales account")].bucket == "turnover"


def test_safe_legacy_mapping_remains_compatible(tmp_path: Path) -> None:
    path = tmp_path / "m.csv"
    path.write_text("account,bucket\nSales,turnover\n'Sales,other_income\n", encoding="utf-8")
    rows = mapping.read_mapping(path)
    assert rows[_digest("sales")].account == "Sales"
    assert rows[_digest("'sales")].account == "'Sales"


@pytest.mark.parametrize("account", ["=cmd|calc", "'=cmd|calc"])
def test_legacy_formula_or_ambiguous_guarded_account_requires_regeneration(
    tmp_path: Path, account: str
) -> None:
    path = tmp_path / "m.csv"
    path.write_text(f"account,bucket\n{account},other_expense\n", encoding="utf-8")
    with pytest.raises(MappingError) as excinfo:
        mapping.read_mapping(path)
    message = str(excinfo.value).casefold()
    assert "regenerate" in message
    assert "reapply" in message


@pytest.mark.parametrize(
    "text",
    [
        "account,account_key,account_key,bucket\nSales,"
        + _digest("sales")
        + ","
        + _digest("sales")
        + ",turnover\n",
        "account,account_key,bucket,source,amount,note\nSales,"
        + _digest("sales")
        + ",turnover\n",
        "account,account_key,bucket\nSales,"
        + _digest("sales")
        + ",turnover,unexpected\n",
    ],
)
def test_malformed_keyed_csv_shape_is_rejected(tmp_path: Path, text: str) -> None:
    path = tmp_path / "m.csv"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(MappingError, match="duplicate|column|truncated|extra"):
        mapping.read_mapping(path)


@pytest.mark.parametrize("blank_header", ["", "   "], ids=["empty", "whitespace"])
def test_populated_unnamed_mapping_column_is_rejected(
    tmp_path: Path, blank_header: str
) -> None:
    path = tmp_path / "m.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("account", blank_header, "account_key", "bucket"))
        writer.writerow(("Sales", "hidden", _digest("sales"), "turnover"))

    with pytest.raises(MappingError, match="unnamed column"):
        mapping.read_mapping(path)


def test_named_mapping_extension_column_remains_accepted(tmp_path: Path) -> None:
    path = tmp_path / "m.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("account", "account_key", "bucket", "review_status"))
        writer.writerow(("Sales", _digest("sales"), "turnover", "approved"))

    rows = mapping.read_mapping(path)

    assert rows[_digest("sales")].bucket == "turnover"


def test_extra_empty_trailing_mapping_cells_remain_accepted(tmp_path: Path) -> None:
    path = tmp_path / "m.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("account", "account_key", "bucket"))
        writer.writerow(("Sales", _digest("sales"), "turnover", "", ""))

    rows = mapping.read_mapping(path)

    assert rows[_digest("sales")].bucket == "turnover"


@pytest.mark.parametrize(
    ("first", "second"),
    [("Sales", " SALES "), ("Straße", "STRASSE")],
)
def test_normalisation_equivalent_mapping_accounts_are_duplicates(
    tmp_path: Path, first: str, second: str
) -> None:
    path = tmp_path / "m.csv"
    path.write_text(
        f"account,bucket\n{first},turnover\n{second},turnover\n", encoding="utf-8"
    )
    with pytest.raises(MappingError, match="more than once"):
        mapping.read_mapping(path)


def test_composed_and_decomposed_unicode_accounts_remain_distinct(tmp_path: Path) -> None:
    path = tmp_path / "m.csv"
    path.write_text("account,bucket\né,turnover\ne\u0301,other_income\n", encoding="utf-8")
    rows = mapping.read_mapping(path)
    assert set(rows) == {_digest("é"), _digest("e\u0301")}


def test_write_rejects_a_forced_digest_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mapping, "account_key", lambda _account: "a" * 64, raising=False)
    with pytest.raises(MappingError, match="collision"):
        mapping.write_mapping(
            tmp_path / "m.csv",
            [
                MappingRow("Sales", "turnover", "reviewed"),
                MappingRow("Rent", "rent", "reviewed"),
            ],
        )


def test_read_rejects_a_forced_digest_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mapping, "account_key", lambda _account: "a" * 64, raising=False)
    path = tmp_path / "m.csv"
    _write_keyed_rows(
        path,
        [
            ["Sales", "a" * 64, "turnover", "reviewed", "1", ""],
            ["Rent", "a" * 64, "rent", "reviewed", "1", ""],
        ],
    )
    with pytest.raises(MappingError, match="collision"):
        mapping.read_mapping(path)
