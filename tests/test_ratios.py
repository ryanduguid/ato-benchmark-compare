from __future__ import annotations

from decimal import Decimal

import pytest

from atobenchmark import to_evidenced_dict
from atobenchmark import dataset as ds
from atobenchmark.ratios import (
    RatioError,
    TURNOVER_FROM_SALES,
    TURNOVER_FROM_TOTAL_INCOME,
    compute,
)
from atobenchmark.report import ABOVE, BELOW, WITHIN, compare


def totals(**kwargs: str) -> dict[str, Decimal]:
    return {name: Decimal(value) for name, value in kwargs.items()}


def test_turnover_uses_sales_when_sales_dominate() -> None:
    figures = compute(totals(turnover="850000", other_income="1200", other_expense="100"))
    assert figures.turnover == Decimal("850000")
    assert figures.turnover_basis == TURNOVER_FROM_SALES


def test_turnover_falls_back_when_sales_are_under_half_of_total_income() -> None:
    # ATO: "If the amount reported in these labels is blank, zero, or less than 50% of
    # the amount at the total business income label, we use the total business income."
    figures = compute(totals(turnover="40000", other_income="100000", other_expense="1"))
    assert figures.turnover == Decimal("140000")
    assert figures.turnover_basis == TURNOVER_FROM_TOTAL_INCOME


def test_sales_at_exactly_half_of_total_income_still_count_as_sales() -> None:
    # "less than 50%" is strict, so a half share is not a fallback.
    figures = compute(totals(turnover="50000", other_income="50000", other_expense="1"))
    assert figures.turnover == Decimal("50000")
    assert figures.turnover_basis == TURNOVER_FROM_SALES


def test_zero_sales_falls_back_to_total_business_income() -> None:
    figures = compute(totals(turnover="0", other_income="90000", other_expense="1"))
    assert figures.turnover == Decimal("90000")
    assert figures.turnover_basis == TURNOVER_FROM_TOTAL_INCOME


def test_no_income_at_all_is_an_error_not_a_division_by_zero() -> None:
    with pytest.raises(RatioError):
        compute(totals(other_expense="5000"))


def test_total_expenses_deducts_payments_to_associated_persons() -> None:
    figures = compute(
        totals(turnover="100000", other_expense="50000", associated_persons="20000")
    )
    assert figures.total_expenses_reported == Decimal("70000")
    assert figures.total_expenses_for_ratio == Decimal("50000")
    assert figures.ratios["total_expenses_to_turnover"] == Decimal("0.5000")


def test_cost_of_sales_ratio_excludes_wages_inside_cost_of_sales() -> None:
    figures = compute(
        totals(turnover="100000", cost_of_sales="30000", cost_of_sales_labour="10000")
    )
    assert figures.cost_of_sales_for_ratio == Decimal("30000")
    assert figures.ratios["cost_of_sales_to_turnover"] == Decimal("0.3000")
    # The wages are still expenses, so they remain in the total expenses ratio.
    assert figures.total_expenses_reported == Decimal("40000")


def test_labour_adds_contractors_without_deducting_associates() -> None:
    # The mapped wage buckets already exclude payments to associates, so deducting
    # the associated_persons bucket here would remove them twice.
    figures = compute(
        totals(
            turnover="100000",
            salary_wages="30000",
            cost_of_sales_labour="5000",
            contractor_commission="8000",
            associated_persons="3000",
        )
    )
    assert figures.labour == Decimal("43000")


def test_w1_replaces_salary_and_wages_when_greater() -> None:
    figures = compute(
        totals(turnover="100000", salary_wages="30000", contractor_commission="1000"),
        w1=Decimal("36000"),
    )
    assert figures.labour == Decimal("37000")
    assert any("W1" in warning for warning in figures.warnings)


def test_w1_is_ignored_when_not_greater() -> None:
    figures = compute(totals(turnover="100000", salary_wages="30000"), w1=Decimal("29000"))
    assert figures.labour == Decimal("30000")


def test_w1_is_compared_and_applied_net_of_associates() -> None:
    # W1 includes wages paid to associates and the mapped salary and wages do not,
    # so both the comparison and the substitution happen net of associate payments.
    ignored = compute(
        totals(turnover="100000", salary_wages="30000", associated_persons="4000"),
        w1=Decimal("33000"),
    )
    assert ignored.labour == Decimal("30000")
    applied = compute(
        totals(turnover="100000", salary_wages="30000", associated_persons="4000"),
        w1=Decimal("36000"),
    )
    assert applied.labour == Decimal("32000")


def test_negative_w1_is_refused() -> None:
    with pytest.raises(RatioError):
        compute(totals(turnover="100000"), w1=Decimal("-1"))


def test_negative_expense_bucket_is_flagged() -> None:
    figures = compute(totals(turnover="100000", other_expense="-500"))
    assert any("negative" in warning for warning in figures.warnings)


def test_zero_associated_persons_is_flagged() -> None:
    figures = compute(totals(turnover="100000", other_expense="500"))
    assert any("associated persons" in warning for warning in figures.warnings)


def test_ratio_is_held_to_two_decimal_places_of_a_percentage() -> None:
    figures = compute(totals(turnover="3", cost_of_sales="1"))
    assert figures.ratios["cost_of_sales_to_turnover"] == Decimal("0.3333")


def test_verdict_boundaries_are_inclusive() -> None:
    data = ds.load("2023-24")
    bakery = data.get("Bakeries and hot bread shops")
    # High band cost of sales is 29% to 36%.
    at_bottom = compute(totals(turnover="1000000", cost_of_sales="290000"))
    at_top = compute(totals(turnover="1000000", cost_of_sales="360000"))
    assert compare(data, bakery, at_bottom).key_verdict.status == WITHIN
    assert compare(data, bakery, at_top).key_verdict.status == WITHIN


def test_just_outside_the_boundary_is_reported_as_outside() -> None:
    data = ds.load("2023-24")
    bakery = data.get("Bakeries and hot bread shops")
    low = compute(totals(turnover="1000000", cost_of_sales="289000"))
    high = compute(totals(turnover="1000000", cost_of_sales="365000"))
    assert compare(data, bakery, low).key_verdict.status == BELOW
    assert compare(data, bakery, high).key_verdict.status == ABOVE
    assert compare(data, bakery, high).outside_key_range is True


def test_displayed_figure_and_verdict_never_disagree() -> None:
    # 30.96% is below a 31% floor. The report must not round it to 31% and then call
    # it below the range.
    data = ds.load("2023-24")
    bakery = data.get("Bakeries and hot bread shops")
    figures = compute(totals(turnover="100000", cost_of_sales="30960"))
    comparison = compare(data, bakery, figures)
    assert comparison.key_verdict.ratio == Decimal("0.3096")
    assert comparison.key_verdict.status == BELOW


def test_key_ratio_switches_when_no_cost_of_sales_is_mapped() -> None:
    data = ds.load("2023-24")
    bakery = data.get("Bakeries and hot bread shops")
    figures = compute(totals(turnover="500000", other_expense="400000"))
    comparison = compare(data, bakery, figures)
    assert comparison.key_ratio == "total_expenses_to_turnover"
    assert comparison.key_verdict.key == "total_expenses_to_turnover"
    assert any("no cost of sales was mapped" in note for note in comparison.notes)


def test_turnover_below_every_band_produces_no_verdict() -> None:
    data = ds.load("2023-24")
    bakery = data.get("Bakeries and hot bread shops")
    figures = compute(totals(turnover="40000", cost_of_sales="12000"))
    comparison = compare(data, bakery, figures)
    assert comparison.band is None
    assert comparison.outside_key_range is False
    assert any("below the lowest published range" in note for note in comparison.notes)


def test_service_industry_reports_no_cost_of_sales_benchmark() -> None:
    data = ds.load("2023-24")
    architects = data.get("Architectural services")
    figures = compute(totals(turnover="500000", cost_of_sales="50000", other_expense="200000"))
    comparison = compare(data, architects, figures)
    statuses = {verdict.key: verdict.status for verdict in comparison.verdicts}
    assert statuses["cost_of_sales_to_turnover"] == "no benchmark in this dataset"
    assert comparison.key_ratio == "total_expenses_to_turnover"


def test_checks_expose_stable_codes_and_required_inputs() -> None:
    figures = compute(
        totals(
            turnover="100000",
            salary_wages="30000",
            cost_of_sales_labour="5000",
            associated_persons="1000",
        ),
        w1=Decimal("40000"),
    )

    check = next(item for item in figures.warning_details if item.code == "w1_used_for_labour")
    assert check.text in figures.warnings
    assert check.required_fields == frozenset(
        {"w1", "salary_wages", "cost_of_sales_labour", "associated_persons"}
    )


def test_comparison_notes_expose_stable_codes_and_required_inputs() -> None:
    data = ds.load("2023-24")
    bakery = data.get("Bakeries and hot bread shops")
    comparison = compare(data, bakery, compute(totals(turnover="40000", cost_of_sales="1")))

    note = next(item for item in comparison.note_details if item.code == "turnover_below_range")
    assert note.text in comparison.notes
    assert note.required_fields == frozenset({"turnover", "other_income"})


def test_evidenced_dict_withholds_unknown_denominator_and_dependent_prose() -> None:
    data = ds.load("2023-24")
    bakery = data.get("Bakeries and hot bread shops")
    comparison = compare(
        data,
        bakery,
        compute(totals(turnover="50000", cost_of_sales="15000")),
    )

    payload = to_evidenced_dict(
        comparison,
        {"turnover", "cost_of_sales"},
    )

    assert payload["turnover"] is None
    assert payload["turnover_basis"] is None
    assert payload["turnover_band"] is None
    assert payload["figures"]["other_business_income"] is None
    assert all(row["status"] == "not_supplied" for row in payload["ratios"])
    assert all(row["benchmark_min"] is None for row in payload["ratios"])
    assert not any("50,000.00" in note for note in payload["notes"])
    assert "other_income" in payload["omitted_buckets"]


def test_evidenced_dict_keeps_only_ratios_with_complete_inputs() -> None:
    data = ds.load("2023-24")
    bakery = data.get("Bakeries and hot bread shops")
    comparison = compare(
        data,
        bakery,
        compute(totals(turnover="850000", other_income="0", cost_of_sales="270000")),
    )

    payload = to_evidenced_dict(
        comparison,
        {"turnover", "other_income", "cost_of_sales"},
    )
    rows = {row["ratio"]: row for row in payload["ratios"]}

    assert rows["cost_of_sales_to_turnover"]["status"] == "within"
    assert rows["rent_to_turnover"]["status"] == "not_supplied"
    assert payload["bucket_totals"]["rent"] is None
    assert payload["figures"]["total_expenses"] is None
    assert payload["complete_buckets"] is False
    assert payload["unreviewed_accounts"] is None


def test_evidenced_dict_filters_checks_by_structured_dependencies() -> None:
    data = ds.load("2023-24")
    bakery = data.get("Bakeries and hot bread shops")
    comparison = compare(
        data,
        bakery,
        compute(
            totals(
                turnover="850000",
                other_income="0",
                cost_of_sales_labour="500",
                associated_persons="0",
                rent="-500",
            ),
            w1=Decimal("200000"),
        ),
    )

    payload = to_evidenced_dict(
        comparison,
        {
            "turnover",
            "other_income",
            "cost_of_sales_labour",
            "associated_persons",
            "rent",
            "w1",
        },
    )

    assert not any("salary and wages label" in check for check in payload["checks_to_make"])
    assert any("rent total is negative" in check for check in payload["checks_to_make"])
    assert any("check(s) to make were withheld" in note for note in payload["notes"])
