"""Compare computed ratios against a benchmark and render the result."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .dataset import Band, BusinessType, Dataset, RATIO_LABELS, Range
from .money import money, percent, percent_range
from .ratios import Figures

WITHIN = "within"
BELOW = "below"
ABOVE = "above"
NO_BENCHMARK = "no benchmark in this dataset"
NO_BAND = "no turnover band applies"

DISCLAIMER = (
    "The ATO publishes these benchmarks as ranges, and sitting outside a range is not "
    "of itself a finding that anything is wrong. This tool reports a comparison. It is "
    "not tax advice and it does not decide how any amount should be reported."
)

RATIO_ORDER = (
    "cost_of_sales_to_turnover",
    "total_expenses_to_turnover",
    "labour_to_turnover",
    "rent_to_turnover",
    "motor_vehicle_to_turnover",
)


@dataclass(frozen=True)
class Verdict:
    key: str
    label: str
    ratio: Decimal
    benchmark: Range | None
    status: str
    is_key: bool


@dataclass
class Comparison:
    dataset: Dataset
    business_type: BusinessType
    figures: Figures
    band: Band | None
    verdicts: tuple[Verdict, ...]
    key_ratio: str
    notes: list[str] = field(default_factory=list)

    @property
    def key_verdict(self) -> Verdict | None:
        for verdict in self.verdicts:
            if verdict.is_key:
                return verdict
        return None

    @property
    def outside_key_range(self) -> bool:
        verdict = self.key_verdict
        return verdict is not None and verdict.status in {BELOW, ABOVE}


def compare(dataset: Dataset, business_type: BusinessType, figures: Figures) -> Comparison:
    notes: list[str] = []
    band = business_type.band_for(figures.turnover)
    if band is None:
        lowest = business_type.bands[0]
        if figures.turnover < lowest.turnover_from:
            notes.append(
                f"Turnover of ${money(figures.turnover)} is below the lowest published range "
                f"for this industry ({lowest.label}), so the ATO benchmarks do not apply."
            )
        else:
            notes.append(
                f"Turnover of ${money(figures.turnover)} does not fall in any published range "
                f"for this industry."
            )

    key_ratio = business_type.key_ratio
    if key_ratio == "cost_of_sales_to_turnover" and figures.cost_of_sales_for_ratio == 0:
        key_ratio = "total_expenses_to_turnover"
        notes.append(
            "Cost of sales to turnover is the ATO key range for this industry, but no cost "
            "of sales was mapped. The ATO says to use total expenses to turnover instead "
            "where cost of sales is not reported, so that is the key range used here."
        )
    elif key_ratio == "cost_of_sales_to_turnover":
        notes.append(
            "The ATO says to use total expenses to turnover as the key range instead where "
            "cost of sales is only a small amount. It does not put a figure on small, so "
            "both ranges are reported and that judgement is yours."
        )

    verdicts = []
    for name in RATIO_ORDER:
        ratio = figures.ratios.get(name)
        if ratio is None:
            continue
        benchmark = band.ratios.get(name) if band else None
        if band is None:
            status = NO_BAND
        elif benchmark is None:
            status = NO_BENCHMARK
        elif benchmark.contains(ratio):
            status = WITHIN
        elif ratio < benchmark.minimum:
            status = BELOW
        else:
            status = ABOVE
        verdicts.append(
            Verdict(
                key=name,
                label=RATIO_LABELS.get(name, name),
                ratio=ratio,
                benchmark=benchmark,
                status=status,
                is_key=(name == key_ratio),
            )
        )

    return Comparison(
        dataset=dataset,
        business_type=business_type,
        figures=figures,
        band=band,
        verdicts=tuple(verdicts),
        key_ratio=key_ratio,
        notes=notes,
    )


def render_text(comparison: Comparison, unreviewed: int = 0) -> str:
    figures = comparison.figures
    source = comparison.dataset.source
    lines: list[str] = []
    add = lines.append

    add("ATO small business benchmark comparison")
    add("=" * 39)
    add(f"Business type:  {comparison.business_type.name}")
    add(f"Benchmark year: {comparison.dataset.year}")
    add(f"Turnover:       ${money(figures.turnover)} ({figures.turnover_basis})")
    add(f"Turnover band:  {comparison.band.label if comparison.band else 'none applies'}")
    add("")

    width = max(len(v.label) for v in comparison.verdicts) + 6
    add(f"{'Ratio'.ljust(width)}{'This business'.ljust(15)}{'ATO range'.ljust(18)}Result")
    add("-" * (width + 15 + 18 + 8))
    for verdict in comparison.verdicts:
        label = verdict.label + (" (key)" if verdict.is_key else "")
        benchmark = (
            percent_range(verdict.benchmark.minimum, verdict.benchmark.maximum)
            if verdict.benchmark
            else "-"
        )
        add(f"{label.ljust(width)}{percent(verdict.ratio).ljust(15)}{benchmark.ljust(18)}{verdict.status}")
    add("")

    add("Figures used")
    add(f"  Sales of goods and services   ${money(figures.trading_sales)}")
    add(f"  Other business income         ${money(figures.other_income)}")
    add(f"  Total business income         ${money(figures.total_business_income)}")
    add(f"  Total expenses                ${money(figures.total_expenses_reported)}")
    add(f"  Less payments to associates   ${money(figures.totals['associated_persons'])}")
    add(f"  Total expenses for the ratio  ${money(figures.total_expenses_for_ratio)}")
    add(f"  Cost of sales excluding wages ${money(figures.cost_of_sales_for_ratio)}")
    add(f"  Labour                        ${money(figures.labour)}")
    add("")

    if unreviewed:
        add("Review outstanding")
        add(
            f"  {unreviewed} account(s) still carry the bucket this tool suggested. "
            f"Suggestions are made from account names alone and are not a substitute "
            f"for reading the ledger."
        )
        add("")

    if comparison.notes:
        add("Notes")
        for note in comparison.notes:
            add(f"  - {note}")
        add("")

    if figures.warnings:
        add("Checks to make")
        for warning in figures.warnings:
            add(f"  - {warning}")
        add("")

    add("Source")
    add(f"  {source.get('publisher')}, {source.get('dataset')}, {source.get('resource_name')}")
    add(f"  {source.get('resource_url')}")
    add(f"  Retrieved {source.get('retrieved')}, sha256 {source.get('sha256')}")
    add(f"  Licensed {source.get('licence')} ({source.get('licence_url')})")
    add("")
    add(DISCLAIMER)
    return "\n".join(lines)


def to_dict(comparison: Comparison, unreviewed: int = 0) -> dict:
    figures = comparison.figures
    return {
        "benchmark_year": comparison.dataset.year,
        "business_type": comparison.business_type.name,
        "key_ratio": comparison.key_ratio,
        "turnover": str(figures.turnover),
        "turnover_basis": figures.turnover_basis,
        "turnover_band": None
        if comparison.band is None
        else {"band": comparison.band.band, "label": comparison.band.label},
        "figures": {
            "sales_of_goods_and_services": str(figures.trading_sales),
            "other_business_income": str(figures.other_income),
            "total_business_income": str(figures.total_business_income),
            "total_expenses": str(figures.total_expenses_reported),
            "payments_to_associated_persons": str(figures.totals["associated_persons"]),
            "total_expenses_for_ratio": str(figures.total_expenses_for_ratio),
            "cost_of_sales_for_ratio": str(figures.cost_of_sales_for_ratio),
            "labour": str(figures.labour),
        },
        "bucket_totals": {name: str(value) for name, value in sorted(figures.totals.items())},
        "ratios": [
            {
                "ratio": verdict.key,
                "label": verdict.label,
                "value": str(verdict.ratio),
                "percent": percent(verdict.ratio),
                "benchmark_min": None if verdict.benchmark is None else str(verdict.benchmark.minimum),
                "benchmark_max": None if verdict.benchmark is None else str(verdict.benchmark.maximum),
                "status": verdict.status,
                "is_key_ratio": verdict.is_key,
            }
            for verdict in comparison.verdicts
        ],
        "unreviewed_accounts": unreviewed,
        "notes": list(comparison.notes),
        "checks_to_make": list(figures.warnings),
        "source": dict(comparison.dataset.source),
        "disclaimer": DISCLAIMER,
    }
