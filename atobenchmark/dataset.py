"""Load the shipped ATO benchmark datasets and select the turnover band.

The JSON files under ``atobenchmark/data`` are built by ``tools/build_dataset.py``
from the ATO's own workbook on data.gov.au. Every ratio is stored as a decimal
string so no binary floating point value ever enters the comparison.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
FILENAME_RE = re.compile(r"^benchmarks-(\d{4}-\d{2})\.json$")
SUPPORTED_SCHEMA_VERSION = 1

RATIO_KEYS = ("cost_of_sales_to_turnover", "total_expenses_to_turnover")

RATIO_LABELS = {
    "cost_of_sales_to_turnover": "Cost of sales to turnover",
    "total_expenses_to_turnover": "Total expenses to turnover",
    "labour_to_turnover": "Labour to turnover",
    "rent_to_turnover": "Rent to turnover",
    "motor_vehicle_to_turnover": "Motor vehicle expenses to turnover",
}


class DatasetError(Exception):
    """Raised when a dataset file is missing, malformed or of an unknown schema."""


@dataclass(frozen=True)
class Range:
    minimum: Decimal
    maximum: Decimal

    def contains(self, value: Decimal) -> bool:
        return self.minimum <= value <= self.maximum


@dataclass(frozen=True)
class Band:
    band: str
    label: str
    turnover_from: Decimal
    turnover_from_inclusive: bool
    turnover_to: Decimal | None
    ratios: dict[str, Range | None]

    def contains(self, turnover: Decimal) -> bool:
        if self.turnover_from_inclusive:
            if turnover < self.turnover_from:
                return False
        elif turnover <= self.turnover_from:
            return False
        if self.turnover_to is not None and turnover > self.turnover_to:
            return False
        return True


@dataclass(frozen=True)
class BusinessType:
    name: str
    key_ratio: str
    bands: tuple[Band, ...]

    @property
    def lowest_turnover(self) -> Decimal:
        return self.bands[0].turnover_from

    def band_for(self, turnover: Decimal) -> Band | None:
        for band in self.bands:
            if band.contains(turnover):
                return band
        return None


@dataclass(frozen=True)
class Dataset:
    year: str
    source: dict
    business_types: tuple[BusinessType, ...]

    def names(self) -> list[str]:
        return [bt.name for bt in self.business_types]

    def get(self, name: str) -> BusinessType:
        matches = self.search(name, exact_only=True)
        if len(matches) == 1:
            return matches[0]
        partial = self.search(name)
        if len(partial) == 1:
            return partial[0]
        if not partial:
            raise DatasetError(
                f"no ATO business type matches {name!r}. "
                f"Run 'ato-benchmark-compare industries' to list all "
                f"{len(self.business_types)} of them."
            )
        listed = "\n  ".join(bt.name for bt in partial[:10])
        more = "" if len(partial) <= 10 else f"\n  ... and {len(partial) - 10} more"
        raise DatasetError(f"{name!r} matches more than one business type:\n  {listed}{more}")

    def search(self, term: str, exact_only: bool = False) -> list[BusinessType]:
        needle = normalise(term)
        exact = [bt for bt in self.business_types if normalise(bt.name) == needle]
        if exact or exact_only:
            return exact
        return [bt for bt in self.business_types if needle in normalise(bt.name)]


# Dashes and quotes vary between the ATO workbook, the ATO website and anything a
# user types. The class is written with escapes so this file stays ASCII only.
PUNCTUATION_RE = re.compile("[-\u2010-\u2015\u2018\u2019\u201c\u201d'\"]+")


def normalise(text: str) -> str:
    """Case fold and flatten punctuation so "Fish and chips shops" matches user input."""
    folded = unicodedata.normalize("NFKD", text).casefold()
    folded = PUNCTUATION_RE.sub(" ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def _decimal(value: object, where: str) -> Decimal:
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DatasetError(f"{where}: {value!r} is not a number") from exc
    if not dec.is_finite():
        raise DatasetError(f"{where}: {value!r} is not a finite number")
    return dec


def _range(raw: object, where: str) -> Range | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or "min" not in raw or "max" not in raw:
        raise DatasetError(f"{where}: expected an object with min and max")
    low = _decimal(raw["min"], f"{where}.min")
    high = _decimal(raw["max"], f"{where}.max")
    if low > high:
        raise DatasetError(f"{where}: minimum {low} exceeds maximum {high}")
    return Range(low, high)


def load(year: str | None = None, data_dir: Path | None = None) -> Dataset:
    """Load a benchmark year. Defaults to the most recent year shipped."""
    directory = data_dir or DATA_DIR
    available = available_years(directory)
    if not available:
        raise DatasetError(f"no benchmark data files found in {directory}")
    chosen = year or available[-1]
    path = directory / f"benchmarks-{chosen}.json"
    if not path.is_file():
        raise DatasetError(
            f"no dataset for benchmark year {chosen!r}. Available: {', '.join(available)}"
        )
    return loads(path.read_text(encoding="utf-8"), source_name=str(path))


def loads(text: str, source_name: str = "<string>") -> Dataset:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DatasetError(f"{source_name}: not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise DatasetError(f"{source_name}: expected a JSON object")

    version = raw.get("schema_version")
    if version != SUPPORTED_SCHEMA_VERSION:
        raise DatasetError(
            f"{source_name}: schema version {version!r} is not supported by this release "
            f"(expected {SUPPORTED_SCHEMA_VERSION})"
        )

    year = raw.get("benchmark_year")
    if not isinstance(year, str) or not year:
        raise DatasetError(f"{source_name}: benchmark_year is missing")

    entries = raw.get("business_types")
    if not isinstance(entries, list) or not entries:
        raise DatasetError(f"{source_name}: business_types is missing or empty")

    business_types = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise DatasetError(f"{source_name}: a business type is not an object")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise DatasetError(f"{source_name}: a business type has no name")
        key_ratio = entry.get("key_ratio")
        if key_ratio not in RATIO_KEYS:
            raise DatasetError(f"{source_name}: {name}: unknown key_ratio {key_ratio!r}")
        bands = []
        for band_raw in entry.get("turnover_bands", []):
            if not isinstance(band_raw, dict):
                raise DatasetError(f"{source_name}: {name}: a turnover band is not an object")
            where = f"{source_name}: {name}/{band_raw.get('band')}"
            turnover_to = band_raw.get("turnover_to")
            bands.append(
                Band(
                    band=str(band_raw.get("band")),
                    label=str(band_raw.get("label", "")),
                    turnover_from=_decimal(band_raw.get("turnover_from"), f"{where}.turnover_from"),
                    turnover_from_inclusive=bool(band_raw.get("turnover_from_inclusive")),
                    turnover_to=None
                    if turnover_to is None
                    else _decimal(turnover_to, f"{where}.turnover_to"),
                    ratios={key: _range(band_raw.get(key), f"{where}.{key}") for key in RATIO_KEYS},
                )
            )
        if not bands:
            raise DatasetError(f"{source_name}: {name}: no turnover bands")
        business_types.append(
            BusinessType(name=name.strip(), key_ratio=key_ratio, bands=tuple(bands))
        )

    source = raw.get("source")
    if not isinstance(source, dict):
        raise DatasetError(f"{source_name}: source metadata is missing")

    return Dataset(year=year, source=source, business_types=tuple(business_types))


def available_years(data_dir: Path | None = None) -> list[str]:
    directory = data_dir or DATA_DIR
    if not directory.is_dir():
        return []
    years = []
    for path in directory.iterdir():
        match = FILENAME_RE.match(path.name)
        if match:
            years.append(match.group(1))
    return sorted(years)
