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
import hashlib
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .atomic_io import atomic_text_writer
from .csvsafe import guard
from .pnl import PnlRow

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

FIELDNAMES = ("account", "account_key", "bucket", "source", "amount", "note")

_ACCOUNT_KEY_RE = re.compile(r"[0-9a-f]{64}")

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


@dataclass(frozen=True)
class MappingDraft:
    """Suggested mapping rows and duplicate accounts collapsed into them."""

    rows: tuple[MappingRow, ...]
    duplicates: tuple[str, ...]

    @property
    def needs_review(self) -> int:
        return sum(1 for row in self.rows if row.bucket == REVIEW)


@dataclass(frozen=True)
class RoutingResult:
    """Bucket totals and review notes produced by routing mapped accounts."""

    totals: dict[str, Decimal]
    unreviewed: int
    notes: tuple[str, ...]


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
    """Established logical identity for an account name."""
    return re.sub(r"\s+", " ", account).strip().casefold()


def account_key(account: str) -> str:
    """Stable digest of the established logical account identity."""
    identity = normalise_account(account)
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def suggest_mapping(rows: Iterable[PnlRow]) -> MappingDraft:
    """Build reviewable mapping rows from parsed profit and loss rows."""
    suggested: list[MappingRow] = []
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for row in rows:
        identity = normalise_account(row.account)
        key = account_key(row.account)
        if key in seen:
            if seen[key] != identity:
                raise MappingError(
                    f"account identity hash collision between {seen[key]!r} and "
                    f"{identity!r}; no mapping was written"
                )
            duplicates.append(row.account)
            continue
        seen[key] = identity
        if row.is_total:
            bucket, note = "excluded", "looks like a subtotal row, so it is left out"
        else:
            bucket, note = suggest(row.account, row.section)
        suggested.append(
            MappingRow(
                account=row.account,
                bucket=bucket,
                source=SOURCE_SUGGESTED,
                note=note,
                amount=str(row.amount),
            )
        )

    return MappingDraft(rows=tuple(suggested), duplicates=tuple(duplicates))


def route(
    rows: Iterable[PnlRow], mapping: dict[str, MappingRow], flip: bool
) -> RoutingResult:
    """Route parsed profit and loss rows through a reviewed mapping."""
    totals = {name: Decimal(0) for name in BUCKETS}
    notes: list[str] = []
    unreviewed = 0
    missing: list[str] = []
    repeated: list[str] = []
    counted: set[str] = set()

    for row in rows:
        identity = normalise_account(row.account)
        key = account_key(row.account)
        entry = mapping.get(key)
        if entry is None:
            missing.append(f"line {row.line_number}: {row.account}")
            continue
        if normalise_account(entry.account) != identity:
            raise MappingError(
                f"line {row.line_number}: account identity hash collision between "
                f"{entry.account!r} and {row.account!r}; no amount was routed"
            )
        if key in counted:
            # One mapping row cannot answer for two ledger rows with the same name, and
            # guessing which bucket the second one belongs to is exactly the silent
            # error this tool exists to avoid.
            repeated.append(f"line {row.line_number}: {row.account}")
            continue
        counted.add(key)
        if entry.source.strip().casefold() == SOURCE_SUGGESTED:
            unreviewed += 1
        if row.is_total and entry.bucket != "excluded":
            notes.append(
                f"{row.account!r} looks like a subtotal row but is mapped to "
                f"{entry.bucket}. The mapping wins, so check it is not double counting."
            )
        amount = row.amount
        if flip and entry.bucket in EXPENSE_BUCKETS:
            amount = -amount
        totals[entry.bucket] += amount

    if missing:
        listed = "\n  ".join(missing[:20])
        more = "" if len(missing) <= 20 else f"\n  ... and {len(missing) - 20} more"
        raise MappingError(
            f"these profit and loss rows have no mapping entry:\n  {listed}{more}\n"
            f"Rerun the map command, or add them to the mapping file."
        )
    if repeated:
        listed = "\n  ".join(repeated[:20])
        more = "" if len(repeated) <= 20 else f"\n  ... and {len(repeated) - 20} more"
        raise MappingError(
            f"these account names appear more than once in the export, so one mapping "
            f"row cannot cover them:\n  {listed}{more}\n"
            f"Give them distinct names in the export, or combine them into one row."
        )

    unused = sorted(set(mapping) - counted)
    if unused:
        notes.append(
            f"{len(unused)} mapping row(s) did not match any account in the export: "
            f"{', '.join(mapping[key].account for key in unused[:5])}"
        )
    return RoutingResult(totals=totals, unreviewed=unreviewed, notes=tuple(notes))


def _duplicate_or_collision(
    path: Path, number: int | None, account: str, previous: str
) -> MappingError:
    location = f"{path} line {number}" if number is not None else str(path)
    if normalise_account(previous) == normalise_account(account):
        return MappingError(f"{location}: {account!r} appears more than once")
    return MappingError(
        f"{location}: account identity hash collision between {previous!r} and {account!r}; "
        "no mapping was applied"
    )


def _resolve_keyed_account(path: Path, number: int, displayed: str, supplied_key: str) -> str:
    if _ACCOUNT_KEY_RE.fullmatch(supplied_key) is None:
        raise MappingError(
            f"{path} line {number}: account_key must be exactly 64 lower-case hexadecimal "
            "characters. Regenerate the mapping and reapply the reviewed bucket, source "
            "and note values."
        )

    candidates: list[str] = []
    if guard(displayed) == displayed:
        candidates.append(displayed)
    if displayed.startswith("'"):
        unguarded = displayed[1:]
        if guard(unguarded) == displayed:
            candidates.append(unguarded)

    matches = [
        candidate
        for candidate in candidates
        if normalise_account(candidate) and account_key(candidate) == supplied_key
    ]
    if not matches:
        raise MappingError(
            f"{path} line {number}: account display and account_key do not identify the "
            "same safely guarded account. Regenerate the mapping and reapply the reviewed "
            "bucket, source and note values."
        )
    if len(matches) > 1:
        raise MappingError(
            f"{path} line {number}: account display and account_key are ambiguous, which "
            "may indicate an account identity hash collision. Regenerate the mapping and "
            "reapply the reviewed bucket, source and note values."
        )
    return matches[0]


def _resolve_legacy_account(path: Path, number: int, displayed: str) -> str:
    guarded_candidate = (
        displayed.startswith("'") and guard(displayed[1:]) == displayed
    )
    if guard(displayed) != displayed or guarded_candidate:
        raise MappingError(
            f"{path} line {number}: legacy account {displayed!r} is formula-like or could "
            "be a spreadsheet guard. Regenerate the mapping and reapply the reviewed "
            "bucket, source and note values."
        )
    return displayed


def write_mapping(path: Path, rows: Iterable[MappingRow]) -> None:
    prepared: list[tuple[MappingRow, str]] = []
    seen: dict[str, str] = {}
    for row in rows:
        if not normalise_account(row.account):
            raise MappingError(
                f"{path}: mapping account cannot be empty after normalisation"
            )
        key = account_key(row.account)
        previous = seen.get(key)
        if previous is not None:
            raise _duplicate_or_collision(path, None, row.account, previous)
        seen[key] = row.account
        prepared.append((row, key))

    with atomic_text_writer(path, encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDNAMES)
        for row, key in prepared:
            writer.writerow(
                [
                    guard(row.account),
                    key,
                    guard(row.bucket),
                    guard(row.source),
                    guard(row.amount),
                    guard(row.note),
                ]
            )


def read_mapping(path: Path) -> dict[str, MappingRow]:
    """Read a mapping file into a dict keyed by stable account digest."""
    if not path.is_file():
        raise MappingError(f"mapping file not found: {path}")
    try:
        # Decoded from bytes rather than read_text() so that no newline translation
        # happens: an account name can carry a quoted newline, and the reader is given
        # newline="" for the same reason a file handle would be.
        data = path.read_bytes()
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        # UnicodeDecodeError is a ValueError, not a MappingError, so left alone it ends
        # the run with a traceback rather than an error line naming the file. utf-8-sig
        # strips the byte-order mark before decoding, so exc.start counts from the text
        # after it. Add those three bytes back, or the position names nothing the
        # operator can find in the file.
        offset = exc.start + (3 if data.startswith(b"\xef\xbb\xbf") else 0)
        raise MappingError(
            f"{path}: not valid UTF-8 text at byte {offset}. Re-save the mapping as "
            f"CSV UTF-8. A Windows export is usually cp1252, which this tool cannot read."
        ) from exc
    with io.StringIO(text, newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise MappingError(f"{path}: file is empty") from None

        names = [(name or "").strip().casefold() for name in header]
        unnamed = [str(index) for index, name in enumerate(names, start=1) if not name]
        if unnamed:
            raise MappingError(
                f"{path}: unnamed column(s) at position(s): {', '.join(unnamed)}"
            )
        duplicates = sorted({name for name in names if name and names.count(name) > 1})
        if duplicates:
            raise MappingError(
                f"{path}: duplicate column name(s): {', '.join(duplicates)}"
            )
        missing = {"account", "bucket"} - set(names)
        if missing:
            raise MappingError(
                f"{path}: missing required column(s): {', '.join(sorted(missing))}. "
                f"Found: {', '.join(name for name in header if name)}"
            )
        keyed = "account_key" in names
        width = len(header)

        rows: dict[str, MappingRow] = {}
        for number, raw in enumerate(reader, start=2):
            if not any(cell.strip() for cell in raw):
                continue
            if len(raw) < width:
                raise MappingError(
                    f"{path} line {number}: truncated row has {len(raw)} column(s); "
                    f"the header has {width}"
                )
            if len(raw) > width:
                if any(cell != "" for cell in raw[width:]):
                    raise MappingError(
                        f"{path} line {number}: populated extra column(s) do not match "
                        "the mapping header"
                    )
                raw = raw[:width]
            record = {name: value for name, value in zip(names, raw) if name}
            displayed = record.get("account", "")
            if not normalise_account(displayed):
                raise MappingError(
                    f"{path} line {number}: mapping account is empty after normalisation"
                )

            if keyed:
                supplied_key = record.get("account_key", "")
                account = _resolve_keyed_account(path, number, displayed, supplied_key)
                key = supplied_key
            else:
                account = _resolve_legacy_account(path, number, displayed)
                key = account_key(account)

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
            previous = rows.get(key)
            if previous is not None:
                raise _duplicate_or_collision(path, number, account, previous.account)
            rows[key] = MappingRow(
                account=account,
                bucket=bucket,
                source=source,
                note=record.get("note", "").strip(),
                amount=record.get("amount", "").strip(),
            )
    if not rows:
        raise MappingError(f"{path}: no mapping rows found")
    return rows
