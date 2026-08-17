"""The ATO benchmark ratio calculations.

Source: Australian Taxation Office, "How we calculate benchmark ratios", QC 37143,
last updated 16 March 2026. The rules this module implements are:

* Every tax return benchmark ratio is a percentage of turnover excluding GST.
* Turnover is the amount at the sales of goods and services label. If that amount is
  blank, zero, or less than 50% of total business income, total business income is
  used instead.
* Total expenses for the ratio is total expenses less payments to associated persons.
* Cost of sales for the ratio excludes salary and wages.
* Labour is total salary and wages plus contractor, subcontractor and commission
  expenses, less payments to associated persons. The return's salary and wages
  label includes associates; the mapping keeps them in their own bucket, so the
  label is reconstructed by adding the associates bucket back. Where the activity
  statement W1 amount is greater than that label, W1 is used instead. Associates
  are deducted exactly once, from whichever figure is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from .mapping import BUCKETS, EXPENSE_BUCKETS

#: Ratios are held to four decimal places, which is two decimal places as a
#: percentage. The comparison and the printed figure therefore always agree.
RATIO_PLACES = Decimal("0.0001")

TURNOVER_FROM_SALES = "sales of goods and services"
TURNOVER_FROM_TOTAL_INCOME = "total business income"


class RatioError(Exception):
    """Raised when the figures cannot produce a comparison."""


@dataclass
class Figures:
    totals: dict[str, Decimal]
    turnover: Decimal
    turnover_basis: str
    trading_sales: Decimal
    other_income: Decimal
    total_business_income: Decimal
    total_expenses_reported: Decimal
    total_expenses_for_ratio: Decimal
    cost_of_sales_for_ratio: Decimal
    labour: Decimal
    ratios: dict[str, Decimal] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def quantise(value: Decimal) -> Decimal:
    return value.quantize(RATIO_PLACES, rounding=ROUND_HALF_UP)


def compute(totals: dict[str, Decimal], w1: Decimal | None = None) -> Figures:
    """Turn bucket totals into ATO benchmark ratios."""
    amounts = {bucket: Decimal(totals.get(bucket, 0)) for bucket in BUCKETS}
    warnings: list[str] = []

    trading_sales = amounts["turnover"]
    other_income = amounts["other_income"]
    total_business_income = trading_sales + other_income

    if trading_sales <= 0 or trading_sales * 2 < total_business_income:
        turnover = total_business_income
        basis = TURNOVER_FROM_TOTAL_INCOME
        if trading_sales <= 0:
            warnings.append(
                "No positive sales of goods and services were mapped, so total business "
                "income is used as turnover. That is the ATO rule, but check the mapping."
            )
        else:
            warnings.append(
                "Sales of goods and services are less than half of total business income, "
                "so total business income is used as turnover."
            )
    else:
        turnover = trading_sales
        basis = TURNOVER_FROM_SALES

    if turnover <= 0:
        raise RatioError(
            "turnover is zero or negative, so no ratio can be calculated. Check that "
            "income accounts are mapped to turnover or other_income and are positive."
        )

    total_expenses_reported = sum(
        (amounts[bucket] for bucket in sorted(EXPENSE_BUCKETS)), Decimal(0)
    )
    associated = amounts["associated_persons"]
    total_expenses_for_ratio = total_expenses_reported - associated
    cost_of_sales_for_ratio = amounts["cost_of_sales"]

    # The ATO compares W1 against the return's salary and wages label, which
    # includes payments to associated persons, then deducts associates from
    # whichever figure is used. The mapped buckets keep associates in their own
    # bucket, so the label is reconstructed by adding them back, and associates
    # are deducted exactly once, at the end.
    salary_wages_mapped = amounts["salary_wages"] + amounts["cost_of_sales_labour"]
    salary_and_wages = salary_wages_mapped + associated
    if w1 is not None:
        if w1 < 0:
            raise RatioError("the activity statement W1 amount cannot be negative")
        if w1 > salary_and_wages:
            warnings.append(
                f"Activity statement W1 ({w1}) is greater than the salary and wages "
                f"label ({salary_and_wages}, the mapped salary and wages plus payments "
                f"to associates), so W1 is used in the labour ratio."
            )
            salary_and_wages = w1
    labour = salary_and_wages - associated + amounts["contractor_commission"]

    for bucket in sorted(EXPENSE_BUCKETS):
        if amounts[bucket] < 0:
            warnings.append(
                f"The {bucket} total is negative ({amounts[bucket]}). Check the sign "
                f"convention of the export, or use --flip-expense-signs."
            )
    if labour < 0:
        warnings.append(
            "Labour is negative. Check the sign convention of the wage and contractor "
            "accounts, or use --flip-expense-signs."
        )
    if cost_of_sales_for_ratio > 0 and amounts["cost_of_sales_labour"] == 0:
        warnings.append(
            "No salary and wages were mapped inside cost of sales. The ATO excludes wages "
            "from the cost of sales ratio, so confirm none are sitting in those accounts."
        )
    if w1 is None and salary_wages_mapped > 0:
        warnings.append(
            "No activity statement W1 amount was supplied. The ATO uses W1 for the labour "
            "ratio when it exceeds the salary and wages figure. Pass --w1 to apply that rule."
        )
    if associated == 0:
        warnings.append(
            "No payments to associated persons were mapped. Wages, directors fees and "
            "management fees paid to associates are deducted from total expenses, so a "
            "zero here raises the total expenses ratio."
        )

    ratios = {
        "total_expenses_to_turnover": quantise(total_expenses_for_ratio / turnover),
        "cost_of_sales_to_turnover": quantise(cost_of_sales_for_ratio / turnover),
        "labour_to_turnover": quantise(labour / turnover),
        "rent_to_turnover": quantise(amounts["rent"] / turnover),
        "motor_vehicle_to_turnover": quantise(amounts["motor_vehicle"] / turnover),
    }

    return Figures(
        totals=amounts,
        turnover=turnover,
        turnover_basis=basis,
        trading_sales=trading_sales,
        other_income=other_income,
        total_business_income=total_business_income,
        total_expenses_reported=total_expenses_reported,
        total_expenses_for_ratio=total_expenses_for_ratio,
        cost_of_sales_for_ratio=cost_of_sales_for_ratio,
        labour=labour,
        ratios=ratios,
        warnings=warnings,
    )
