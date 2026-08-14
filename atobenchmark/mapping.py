"""Account buckets, the mapping file, and the suggestions that seed it.

A profit and loss account name does not tell you which ATO label an amount belongs
to. Payments to associated persons look like ordinary wages, cost of sales can carry
labour, and an account called "Fuel" can be a motor vehicle expense or a direct cost
of running plant. So the mapping is a reviewable artefact: this module can suggest a
bucket for each account, but the suggestion is recorded as a suggestion and the
report says so until a person changes it.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

from .atomic_io import atomic_text_writer
from .csvsafe import guard

REVIEW = "REVIEW"

#: Bucket name mapped to what the ATO does with it.
BUCKETS: dict[str, str] = {
    "turnover": "Sales of goods and services. The ATO's turnover label.",
    "other_income": "Business income that is not sales, for example interest or grants.",
    "cost_of_sales": "Cost of sales, excluding any salary and wages inside it.",
    "cost_of_sales_labour": "Salary and wages included in cost of sales.",
    "salary_wages": "Salary and wages outside cost of sales.",
    "contractor_commission": "Contractor, subcontractor and commission expenses.",
    "associated_persons": "Payments to associated persons.",
    "rent": "Rent expenses.",
    "motor_vehicle": "Motor vehicle expenses.",
    "other_expense": "Every other expense, including superannuation and depreciation.",
    "excluded": "Not part of the ATO calculation, for example income tax expense.",
}

INCOME_BUCKETS = frozenset({"turnover", "other_income"})
EXPENSE_BUCKETS = frozenset(
    {
        "cost_of_sales",
        "cost_of_sales_labour",
        "salary_wages",
        "contractor_commission",
        "associated_persons",
        "rent",
        "motor_vehicle",
        "other_expense",
    }
)

FIELDNAMES = ("account", "bucket", "source", "amount", "note")

SOURCE_SUGGESTED = "suggested"
SOURCE_REVIEWED = "reviewed"

# Ordered rules. The first pattern that matches an account name wins, so the more
# specific patterns are listed first. Every rule carries the reason it fired, which
# is written into the mapping file so a reviewer can see why a bucket was proposed.
_RULES: tuple[tuple[str, str, str], ...] = (
    (r"income tax (expense|provision)", "excluded", "income tax is outside the ATO expense labels"),
    (r"associated (person|entity)|related part(y|ies)|spouse", "associated_persons", "named as an associate, related party or spouse"),
    (r"payroll tax", "other_expense", "payroll tax is not salary and wages"),
    (r"(sub[- ]?contractor|contractor)", "contractor_commission", "contractor wording"),
    (r"commission", "contractor_commission", "commission wording"),
    (r"(direct|production|factory) (labour|labor|wages)", "cost_of_sales_labour", "labour inside cost of sales"),
    (r"superannuation|super contribution", "other_expense", "superannuation is its own ATO label"),
    (r"(wages|salaries|salary)", "salary_wages", "salary and wages wording"),
    (r"cost of (sales|goods)|opening stock|closing stock|purchases", "cost_of_sales", "cost of sales wording"),
    (r"motor vehicle|vehicle running|car expense", "motor_vehicle", "motor vehicle wording"),
    (r"rent(al)? (received|income)", "other_income", "rent received is income"),
    (r"\brent\b", "rent", "rent wording"),
    (r"interest (income|received)|dividend|government (grant|payment)|fuel tax credit", "other_income", "not sales of goods or services"),
    (r"(gain|profit) on (sale|disposal)", "other_income", "not sales of goods or services"),
    # "Fees" on its own is ambiguous: accounting fees and bank fees are expenses, so
    # only the income forms of the word count as turnover wording.
    (r"\bsales\b|services income|trading income|revenue|\bfees? income\b|fees charged", "turnover", "sales wording"),
)

_COMPILED = tuple((re.compile(pattern, re.IGNORECASE), bucket, reason) for pattern, bucket, reason in _RULES)


class MappingError(Exception):
    """Raised when a mapping file is unusable."""


@dataclass(frozen=True)
class MappingRow:
    account: str
    bucket: str
    source: str
    note: str = ""
    amount: str = ""


def suggest(account: str, section: str | None = None) -> tuple[str, str]:
    """Propose a bucket for an account name. Returns (bucket, reason)."""
    for pattern, bucket, reason in _COMPILED:
        if pattern.search(account):
            if section == "income" and bucket not in INCOME_BUCKETS:
                # An account sitting in the income section of a profit and loss is
                # income whatever its name suggests, so fall back to the weaker but
                # correct answer rather than filing it as an expense.
                return "turnover", "in the income section"
            if section in {"cost_of_sales", "expense"} and bucket in INCOME_BUCKETS:
                return REVIEW, f"matched {bucket} wording but sits in an expense section"
            if section == "cost_of_sales" and bucket == "salary_wages":
                # The ATO takes salary and wages out of the cost of sales ratio, so
                # wages sitting in the cost of sales section get their own bucket.
                return "cost_of_sales_labour", "wages inside the cost of sales section"
            return bucket, reason
    if section == "income":
        return "turnover", "in the income section"
    if section == "cost_of_sales":
        return "cost_of_sales", "in the cost of sales section, no more specific rule matched"
    if section == "expense":
        return "other_expense", "in the expense section, no more specific rule matched"
    return REVIEW, "no rule matched"


def normalise_account(account: str) -> str:
    """Key used to join a mapping row to a profit and loss row."""
    return re.sub(r"\s+", " ", account).strip().casefold()


def write_mapping(path: Path, rows: list[MappingRow]) -> None:
    with atomic_text_writer(path, encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDNAMES)
        for row in rows:
            writer.writerow(
                [guard(row.account), guard(row.bucket), guard(row.source), guard(row.amount), guard(row.note)]
            )


def read_mapping(path: Path) -> dict[str, MappingRow]:
    """Read a mapping file into a dict keyed by normalised account name."""
    if not path.is_file():
        raise MappingError(f"mapping file not found: {path}")
    text = path.read_text(encoding="utf-8-sig")
    # io.StringIO rather than splitlines(), so a quoted newline inside an account
    # name does not split the row. restval keeps a truncated row visible.
    reader = csv.DictReader(io.StringIO(text), restval="")
    if reader.fieldnames is None:
        raise MappingError(f"{path}: file is empty")
    missing = {"account", "bucket"} - {(name or "").strip().casefold() for name in reader.fieldnames}
    if missing:
        raise MappingError(
            f"{path}: missing required column(s): {', '.join(sorted(missing))}. "
            f"Found: {', '.join(name for name in reader.fieldnames if name)}"
        )

    rows: dict[str, MappingRow] = {}
    for number, raw in enumerate(reader, start=2):
        record = {(key or "").strip().casefold(): (value or "") for key, value in raw.items() if key}
        account = record.get("account", "").strip()
        if not account:
            continue
        bucket = record.get("bucket", "").strip()
        if not bucket:
            raise MappingError(f"{path} line {number}: {account!r} has no bucket")
        if bucket == REVIEW:
            raise MappingError(
                f"{path} line {number}: {account!r} is still marked {REVIEW}. "
                f"Choose one of: {', '.join(sorted(BUCKETS))}"
            )
        if bucket not in BUCKETS:
            raise MappingError(
                f"{path} line {number}: {account!r} has unknown bucket {bucket!r}. "
                f"Choose one of: {', '.join(sorted(BUCKETS))}"
            )
        source = record.get("source", "").strip() or SOURCE_REVIEWED
        key = normalise_account(account)
        if key in rows:
            raise MappingError(f"{path} line {number}: {account!r} appears more than once")
        rows[key] = MappingRow(
            account=account, bucket=bucket, source=source, note=record.get("note", "").strip()
        )
    if not rows:
        raise MappingError(f"{path}: no mapping rows found")
    return rows
