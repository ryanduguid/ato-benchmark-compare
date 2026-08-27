"""Direct contracts for mapping suggestion and reviewed amount routing."""

from decimal import Decimal

import pytest

from atobenchmark import cli, mapping
from atobenchmark.mapping import BUCKETS, MappingError, MappingRow
from atobenchmark.pnl import PnlRow


def _key(account: str) -> str:
    return mapping.account_key(account)


def test_suggest_mapping_returns_ordered_rows_duplicates_and_review_count() -> None:
    rows = (
        PnlRow("Total Income", Decimal("100"), line_number=2, section="income", is_total=True),
        PnlRow("Sales Account", Decimal("70"), line_number=3, section="income"),
        PnlRow("Sundry", Decimal("5"), line_number=4),
        PnlRow(" SALES   ACCOUNT ", Decimal("30"), line_number=5, section="income"),
    )

    draft = mapping.suggest_mapping(rows)

    assert draft.rows == (
        MappingRow(
            account="Total Income",
            bucket="excluded",
            source="suggested",
            note="looks like a subtotal row, so it is left out",
            amount="100",
        ),
        MappingRow(
            account="Sales Account",
            bucket="turnover",
            source="suggested",
            note="sales wording",
            amount="70",
        ),
        MappingRow(
            account="Sundry",
            bucket="REVIEW",
            source="suggested",
            note="no rule matched",
            amount="5",
        ),
    )
    assert draft.duplicates == (" SALES   ACCOUNT ",)
    assert draft.needs_review == 1


def test_suggest_mapping_rejects_a_hash_collision_before_returning_a_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mapping, "account_key", lambda _account: "a" * 64)

    with pytest.raises(MappingError) as raised:
        mapping.suggest_mapping(
            (
                PnlRow("Sales", Decimal("100"), line_number=2),
                PnlRow("Rent", Decimal("10"), line_number=3),
            )
        )

    assert str(raised.value) == (
        "account identity hash collision between 'sales' and 'rent'; "
        "no mapping was written"
    )


def test_route_returns_exact_totals_review_count_and_ordered_notes() -> None:
    rows = (
        PnlRow("Sales", Decimal("100"), line_number=2),
        PnlRow("Purchases", Decimal("-32"), line_number=3),
        PnlRow("Total Costs", Decimal("-32"), line_number=4, is_total=True),
    )
    reviewed_mapping = {
        _key("Sales"): MappingRow("Sales", "turnover", "reviewed"),
        _key("Purchases"): MappingRow("Purchases", "cost_of_sales", "suggested"),
        _key("Total Costs"): MappingRow("Total Costs", "cost_of_sales", "reviewed"),
        _key("Rent"): MappingRow("Rent", "rent", "reviewed"),
    }

    result = mapping.route(rows, reviewed_mapping, flip=True)

    expected = {name: Decimal(0) for name in BUCKETS}
    expected["turnover"] = Decimal("100")
    expected["cost_of_sales"] = Decimal("64")
    assert result.totals == expected
    assert result.unreviewed == 1
    assert result.notes == (
        "'Total Costs' looks like a subtotal row but is mapped to cost_of_sales. "
        "The mapping wins, so check it is not double counting.",
        "1 mapping row(s) did not match any account in the export: Rent",
    )


def test_route_preserves_the_exact_missing_account_error() -> None:
    with pytest.raises(MappingError) as raised:
        mapping.route((PnlRow("Sales", Decimal("100"), line_number=2),), {}, flip=False)

    assert str(raised.value) == (
        "these profit and loss rows have no mapping entry:\n"
        "  line 2: Sales\n"
        "Rerun the map command, or add them to the mapping file."
    )


def test_route_preserves_the_exact_repeated_account_error() -> None:
    rows = (
        PnlRow("Sales", Decimal("60"), line_number=2),
        PnlRow(" SALES ", Decimal("40"), line_number=3),
    )
    reviewed_mapping = {_key("Sales"): MappingRow("Sales", "turnover", "reviewed")}

    with pytest.raises(MappingError) as raised:
        mapping.route(rows, reviewed_mapping, flip=False)

    assert str(raised.value) == (
        "these account names appear more than once in the export, so one mapping "
        "row cannot cover them:\n"
        "  line 3:  SALES \n"
        "Give them distinct names in the export, or combine them into one row."
    )


def test_route_rejects_a_hash_collision_before_routing_amounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mapping, "account_key", lambda _account: "a" * 64)
    reviewed_mapping = {"a" * 64: MappingRow("Sales", "turnover", "reviewed")}

    with pytest.raises(MappingError) as raised:
        mapping.route(
            (PnlRow("Rent", Decimal("10"), line_number=7),),
            reviewed_mapping,
            flip=False,
        )

    assert str(raised.value) == (
        "line 7: account identity hash collision between 'Sales' and 'Rent'; "
        "no amount was routed"
    )


def test_mapping_module_owns_suggestion_and_routing_workflows() -> None:
    assert callable(mapping.suggest_mapping)
    assert callable(mapping.route)
    assert not hasattr(cli, "_bucket_totals")
