"""Convert an ATO small business benchmarks workbook into the shipped JSON dataset.

Development-time script. It is not imported by the runtime package and openpyxl is
not a runtime dependency. Run it when the ATO publishes a new benchmark year:

    uv run --with openpyxl python tools/build_dataset.py \
        --xlsx small-business-benchmarks-2023-24-data.xlsx \
        --year 2023-24 \
        --resource-url https://data.gov.au/... \
        --resource-name "2023-24 Benchmarks" \
        --resource-last-modified 2026-03-15T22:07:39.563377 \
        --retrieved 2026-08-13 \
        --out atobenchmark/data/benchmarks-2023-24.json

Every published figure is copied verbatim. The only derived values are the turnover
band bounds, which are read from the ATO's own range labels, and the key ratio, which
follows the rule the ATO states on each industry page: cost of sales to turnover is
the key range where the ATO publishes one, otherwise total expenses to turnover.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

BAND_NAMES = ("low", "medium", "high")

# Column layout of the ATO workbook, zero based.
COL_BUSINESS_TYPE = 0
TOTAL_EXPENSES_COLS = {"low": (1, 2), "medium": (3, 4), "high": (5, 6)}
COST_OF_SALES_COLS = {"low": (7, 8), "medium": (9, 10), "high": (11, 12)}
RANGE_LABEL_COLS = {"low": 13, "medium": 14, "high": 15}

MONEY = r"\$([\d,]+)"
RANGE_RE = re.compile(rf"^{MONEY}\s*[-–—]\s*{MONEY}$")
MORE_THAN_RE = re.compile(rf"^More than {MONEY}$", re.IGNORECASE)


class BuildError(Exception):
    """Raised when the workbook does not match the expected layout."""


def money(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def ratio(value: object, where: str) -> str | None:
    """Return a ratio as an exact decimal string, or None when not published."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    dec = Decimal(str(value))
    if not dec.is_finite():
        raise BuildError(f"{where}: non finite ratio {value!r}")
    if dec < 0 or dec > 10:
        raise BuildError(f"{where}: ratio {dec} is outside any plausible range")
    return str(dec)


def parse_range_label(label: object, where: str) -> tuple[Decimal, Decimal | None] | None:
    """Parse an ATO turnover range label into (printed_from, printed_to)."""
    if label is None:
        return None
    text = str(label).strip()
    if not text or text.upper() == "N/A":
        return None
    match = RANGE_RE.match(text)
    if match:
        low, high = money(match.group(1)), money(match.group(2))
        if low > high:
            raise BuildError(f"{where}: range label {text!r} runs backwards")
        return low, high
    match = MORE_THAN_RE.match(text)
    if match:
        return money(match.group(1)), None
    raise BuildError(f"{where}: unrecognised turnover range label {text!r}")


def build_bands(row: tuple, name: str, anomalies: list[str]) -> list[dict]:
    bands: list[dict] = []
    previous_to: Decimal | None = None
    for band in BAND_NAMES:
        label = row[RANGE_LABEL_COLS[band]]
        parsed = parse_range_label(label, f"{name}/{band}")
        if parsed is None:
            continue
        printed_from, printed_to = parsed
        te_min, te_max = TOTAL_EXPENSES_COLS[band]
        cos_min, cos_max = COST_OF_SALES_COLS[band]

        # The ATO prints adjoining bands as "$50,000 - $150,000" then
        # "$150,001 - $600,000". Read as printed those leave a gap for any turnover
        # between the two whole dollar figures, so the lower bound of every band after
        # the first is the previous band's upper bound, exclusive. That closes the gap
        # without moving any published boundary.
        if previous_to is None:
            turnover_from = printed_from
            from_inclusive = True
        else:
            turnover_from = previous_to
            from_inclusive = False
            if printed_from - previous_to not in (Decimal(0), Decimal(1)):
                anomalies.append(
                    f"{name}/{band}: band starts at {printed_from} but the previous "
                    f"band ends at {previous_to}"
                )

        entry = {
            "band": band,
            "label": str(label).strip(),
            "turnover_from": str(turnover_from),
            "turnover_from_inclusive": from_inclusive,
            "turnover_to": None if printed_to is None else str(printed_to),
            "total_expenses_to_turnover": pair(
                ratio(row[te_min], f"{name}/{band}/total_expenses_min"),
                ratio(row[te_max], f"{name}/{band}/total_expenses_max"),
                f"{name}/{band}/total_expenses",
            ),
            "cost_of_sales_to_turnover": pair(
                ratio(row[cos_min], f"{name}/{band}/cost_of_sales_min"),
                ratio(row[cos_max], f"{name}/{band}/cost_of_sales_max"),
                f"{name}/{band}/cost_of_sales",
            ),
        }
        bands.append(entry)
        previous_to = printed_to
    if not bands:
        raise BuildError(f"{name}: no turnover bands published")
    return bands


def pair(minimum: str | None, maximum: str | None, where: str) -> dict | None:
    if minimum is None and maximum is None:
        return None
    if minimum is None or maximum is None:
        raise BuildError(f"{where}: only one end of the range is published")
    if Decimal(minimum) > Decimal(maximum):
        raise BuildError(f"{where}: minimum {minimum} exceeds maximum {maximum}")
    return {"min": minimum, "max": maximum}


def build(xlsx: Path, args: argparse.Namespace) -> dict:
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - developer tooling only
        raise SystemExit(
            "openpyxl is required to rebuild the dataset: uv run --with openpyxl ..."
        ) from exc

    payload = xlsx.read_bytes()
    workbook = openpyxl.load_workbook(xlsx, data_only=True)
    sheet = workbook.worksheets[0]
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        raise BuildError("workbook is empty")

    header = rows[0]
    if header[COL_BUSINESS_TYPE] is None or "BusinessType" not in str(header[COL_BUSINESS_TYPE]):
        raise BuildError(f"unexpected first header cell: {header[COL_BUSINESS_TYPE]!r}")
    if len(header) < 16:
        raise BuildError(f"expected 16 columns, found {len(header)}")
    for lo, hi in TOTAL_EXPENSES_COLS.values():
        for col in (lo, hi):
            if "Total Expenses" not in str(header[col]):
                raise BuildError(f"column {col} is not a Total Expenses column: {header[col]!r}")
    for lo, hi in COST_OF_SALES_COLS.values():
        for col in (lo, hi):
            if "Cost of Sales" not in str(header[col]):
                raise BuildError(f"column {col} is not a Cost of Sales column: {header[col]!r}")

    anomalies: list[str] = []
    business_types = []
    seen = set()
    for row in rows[1:]:
        name = row[COL_BUSINESS_TYPE]
        if name is None or not str(name).strip():
            continue
        name = str(name).strip()
        if name in seen:
            raise BuildError(f"duplicate business type {name!r}")
        seen.add(name)
        bands = build_bands(row, name, anomalies)
        has_cost_of_sales = any(b["cost_of_sales_to_turnover"] for b in bands)
        business_types.append(
            {
                "name": name,
                "key_ratio": "cost_of_sales_to_turnover"
                if has_cost_of_sales
                else "total_expenses_to_turnover",
                "turnover_bands": bands,
            }
        )

    if anomalies:
        for line in anomalies:
            print(f"anomaly: {line}", file=sys.stderr)

    return {
        "schema_version": 1,
        "benchmark_year": args.year,
        "business_type_count": len(business_types),
        "source": {
            "publisher": "Australian Taxation Office",
            "dataset": "Small Business Benchmarks",
            "dataset_page": "https://data.gov.au/data/dataset/small-business-benchmarks",
            "resource_name": args.resource_name,
            "resource_url": args.resource_url,
            "resource_last_modified": args.resource_last_modified,
            "retrieved": args.retrieved,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "licence": "Creative Commons Attribution 2.5 Australia",
            "licence_url": "https://creativecommons.org/licenses/by/2.5/au/",
        },
        "business_types": business_types,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xlsx", required=True, type=Path)
    parser.add_argument("--year", required=True, help='benchmark year, for example "2023-24"')
    parser.add_argument("--resource-name", required=True)
    parser.add_argument("--resource-url", required=True)
    parser.add_argument("--resource-last-modified", required=True)
    parser.add_argument("--retrieved", required=True, help="ISO date the file was downloaded")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    dataset = build(args.xlsx, args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dataset, indent=1, ensure_ascii=False) + "\n"
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(
        f"wrote {args.out}: {dataset['business_type_count']} business types, "
        f"sha256 {dataset['source']['sha256'][:16]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
