"""Read a profit and loss export into account and amount rows.

Two shapes are supported.

Neutral CSV, which is the format this tool guarantees:

    account,amount
    Sales,850000
    Purchases,290000

Report style CSV, which is what accounting packages export: a title block, section
headings, blank rows, account rows and subtotal rows. That shape is handled on a best
effort basis. Subtotal rows are detected and marked rather than dropped, so a total
can never be silently added to the figures it totals, and nothing disappears without
appearing in the mapping file.

The report style layout is inferred, not verified against a real export from any
particular product. Confirm the header row of your own export before relying on it.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .money import AmountError, parse_amount

SECTION_INCOME = "income"
SECTION_COST_OF_SALES = "cost_of_sales"
SECTION_EXPENSE = "expense"

_SECTION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"^(less\s+)?cost of (sales|goods sold)$", SECTION_COST_OF_SALES),
    (r"^(trading|operating|other)?\s*income$", SECTION_INCOME),
    (r"^revenue$", SECTION_INCOME),
    (r"^(less\s+)?(operating\s+)?expenses$", SECTION_EXPENSE),
    (r"^(less\s+)?administration expenses$", SECTION_EXPENSE),
    (r"^overheads$", SECTION_EXPENSE),
)

_TOTAL_PATTERNS = (
    r"^total\b",
    r"^gross (profit|loss)",
    r"^net (profit|loss|income)",
    r"^operating (profit|loss)",
    r"^(profit|loss) (before|after) (income )?tax",
    r"^earnings before",
)

_SECTION_RE = tuple((re.compile(pattern, re.IGNORECASE), section) for pattern, section in _SECTION_PATTERNS)
_TOTAL_RE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in _TOTAL_PATTERNS)


class PnlError(Exception):
    """Raised when a profit and loss file cannot be read."""


@dataclass(frozen=True)
class PnlRow:
    account: str
    amount: Decimal
    line_number: int
    section: str | None = None
    is_total: bool = False


@dataclass(frozen=True)
class PnlFile:
    rows: tuple[PnlRow, ...]
    layout: str
    amount_column: str
    skipped: tuple[str, ...]

    @property
    def accounts(self) -> tuple[PnlRow, ...]:
        return tuple(row for row in self.rows if not row.is_total)

    @property
    def totals(self) -> tuple[PnlRow, ...]:
        return tuple(row for row in self.rows if row.is_total)


def is_total_row(label: str) -> bool:
    stripped = label.strip()
    return any(pattern.match(stripped) for pattern in _TOTAL_RE)


def section_for(label: str) -> str | None:
    stripped = label.strip().rstrip(":")
    for pattern, section in _SECTION_RE:
        if pattern.match(stripped):
            return section
    return None


def read(path: Path, amount_column: str | None = None) -> PnlFile:
    if not path.is_file():
        raise PnlError(f"profit and loss file not found: {path}")
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise PnlError(f"{path}: file is empty")
    # io.StringIO rather than splitlines(): an account name can contain a quoted
    # newline, and splitlines() would cut the row in half inside the quotes.
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise PnlError(f"{path}: file is empty")

    header = [cell.strip().casefold() for cell in rows[0]]
    if "account" in header and "amount" in header:
        if amount_column is not None:
            # Accepting the option and ignoring it would leave the user believing they
            # had selected a period that this layout does not have.
            raise PnlError(
                f"{path} has an account and amount header, so --amount-column does not "
                f"apply. Remove it, or drop the header row to read the file as a report "
                f"style export."
            )
        return _read_neutral(path, rows, header)
    return _read_report(path, rows, amount_column)


def _read_neutral(path: Path, rows: list[list[str]], header: list[str]) -> PnlFile:
    account_at = header.index("account")
    amount_at = header.index("amount")
    section_at = header.index("section") if "section" in header else None
    width = len(header)

    parsed: list[PnlRow] = []
    skipped: list[str] = []
    for number, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) > width and any(cell.strip() for cell in row[width:]):
            # An extra populated cell means the row does not line up with the header,
            # which usually comes from an unquoted comma inside an account name. Left
            # alone it would read the wrong cell as the amount.
            raise PnlError(
                f"{path} line {number}: expected {width} column(s), found {len(row)}. "
                f"An unquoted comma inside an account name is the usual cause."
            )
        if len(row) < width:
            row = list(row) + [""] * (width - len(row))
        account = row[account_at].strip()
        if not account:
            skipped.append(f"line {number}: no account name")
            continue
        try:
            amount = parse_amount(row[amount_at], f"{path} line {number}")
        except AmountError as exc:
            raise PnlError(str(exc)) from exc
        section = None
        if section_at is not None:
            section = row[section_at].strip().casefold() or None
            if section is not None and section not in {
                SECTION_INCOME,
                SECTION_COST_OF_SALES,
                SECTION_EXPENSE,
            }:
                raise PnlError(
                    f"{path} line {number}: unknown section {section!r}. Use one of "
                    f"{SECTION_INCOME}, {SECTION_COST_OF_SALES}, {SECTION_EXPENSE}."
                )
        parsed.append(
            PnlRow(
                account=account,
                amount=amount,
                line_number=number,
                section=section,
                is_total=is_total_row(account),
            )
        )
    if not parsed:
        raise PnlError(f"{path}: no account rows found")
    return PnlFile(
        rows=tuple(parsed),
        layout="neutral",
        amount_column=rows[0][amount_at].strip() or "amount",
        skipped=tuple(skipped),
    )


def _amount_column_index(rows: list[list[str]], amount_column: str | None) -> tuple[int, str]:
    """Choose which column holds the amounts in a report style export."""
    widest = max((len(row) for row in rows), default=0)
    if widest < 2:
        raise PnlError("report style export needs at least two columns")

    if amount_column is not None:
        if amount_column.isdigit():
            index = int(amount_column)
            if index < 1 or index >= widest:
                raise PnlError(
                    f"--amount-column {amount_column} is out of range: the file has "
                    f"{widest - 1} value column(s)"
                )
            return index, f"column {index}"
        wanted = amount_column.strip().casefold()
        for row in rows[:20]:
            for index, cell in enumerate(row):
                if index > 0 and cell.strip().casefold() == wanted:
                    return index, cell.strip()
        raise PnlError(f"--amount-column {amount_column!r} does not match any column heading")

    # No column named, so use the first column that parses as an amount on more rows
    # than any earlier column. Ties keep the leftmost, which is the current period in
    # a comparative export.
    counts = [0] * widest
    for row in rows:
        for index in range(1, min(len(row), widest)):
            cell = row[index].strip()
            if not cell:
                continue
            try:
                parse_amount(cell)
            except AmountError:
                continue
            counts[index] += 1
    best = max(range(1, widest), key=lambda i: counts[i])
    if counts[best] == 0:
        raise PnlError("no column in this file parses as amounts")
    return best, f"column {best}"


def _read_report(path: Path, rows: list[list[str]], amount_column: str | None) -> PnlFile:
    index, column_name = _amount_column_index(rows, amount_column)
    parsed: list[PnlRow] = []
    skipped: list[str] = []
    section: str | None = None

    for number, row in enumerate(rows, start=1):
        if not any(cell.strip() for cell in row):
            continue
        label = row[0].strip() if row else ""
        cell = row[index].strip() if len(row) > index else ""

        heading = section_for(label) if label else None
        if heading is not None:
            # Some exports print the section total on the heading row itself. The
            # heading still starts a section, and any amount on it is a total, so it
            # is recorded as one rather than becoming an account that double counts
            # everything beneath it.
            section = heading
            if not cell:
                continue
            try:
                amount = parse_amount(cell, f"{path} line {number}")
            except AmountError:
                skipped.append(f"line {number}: {label!r} has no readable amount in {column_name}")
                continue
            parsed.append(
                PnlRow(
                    account=label,
                    amount=amount,
                    line_number=number,
                    section=section,
                    is_total=True,
                )
            )
            continue

        if label and not cell:
            skipped.append(f"line {number}: {label!r} has no amount in {column_name}")
            continue
        if not label:
            skipped.append(f"line {number}: amount with no account name")
            continue

        try:
            amount = parse_amount(cell, f"{path} line {number}")
        except AmountError:
            skipped.append(f"line {number}: {label!r} has no readable amount in {column_name}")
            continue

        parsed.append(
            PnlRow(
                account=label,
                amount=amount,
                line_number=number,
                section=section,
                is_total=is_total_row(label),
            )
        )

    if not parsed:
        raise PnlError(
            f"{path}: no account rows found. If this is a two column file, give it an "
            f"'account,amount' header row."
        )
    return PnlFile(rows=tuple(parsed), layout="report", amount_column=column_name, skipped=tuple(skipped))
