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
    # Decoded from bytes rather than read_text() for the reason read_mapping gives:
    # an account name can carry a quoted newline, and read_text() applies
    # universal-newline translation, which rewrites a quoted CRLF to a bare LF.
    # Matching survives either spelling, because normalise_account collapses \s+ to
    # one space and both reach the same account_key. What read_text() broke is every
    # path that writes the name back out: map drafted a mapping file whose account
    # column held a name the export does not contain.
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        # UnicodeDecodeError is a ValueError, not a PnlError, so left alone it ends the
        # run with a traceback. A "CSV (Comma delimited)" export from a Windows
        # accounting package is cp1252, which fails here on the first accented account
        # name or smart apostrophe. utf-8-sig strips the byte-order mark before
        # decoding, so exc.start counts from the text after it. Add those three bytes
        # back, or the position names nothing the operator can find in the file.
        offset = exc.start + (3 if raw.startswith(b"\xef\xbb\xbf") else 0)
        raise PnlError(
            f"{path}: not valid UTF-8 text at byte {offset}. Re-save the export as "
            f"CSV UTF-8. A Windows export is usually cp1252, which this tool cannot read."
        ) from exc
    if not text.strip():
        raise PnlError(f"{path}: file is empty")
    # io.StringIO rather than splitlines(): an account name can contain a quoted
    # newline, and splitlines() would cut the row in half inside the quotes. newline=""
    # for the same reason a file handle would be given it.
    rows = list(csv.reader(io.StringIO(text, newline="")))
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


def _heading_carries_section_total(later: list[list[str]], index: int, amount: Decimal) -> bool | None:
    """Whether the amount on a section heading row is the total of the rows under it.

    Some exports print the section total on the heading row itself. In that shape the
    detail rows beneath the heading, up to the next heading or total row, add up to
    the amount on the heading. When every detail cell parses and the sum does not
    match, the heading is an ordinary account that happens to be named like a
    section, and excluding it would silently drop a real amount.

    Returns True when the detail rows add up to the amount, False when they all parse
    and provably do not, and None when a detail cell is non blank but unreadable, for
    example a "-" or "nil" placeholder, so the sum cannot be checked either way.
    """
    running = Decimal(0)
    seen_detail = False
    for row in later:
        if not any(cell.strip() for cell in row):
            continue
        label = row[0].strip() if row else ""
        cell = row[index].strip() if len(row) > index else ""
        if label and (section_for(label) is not None or is_total_row(label)):
            break
        if not label or not cell:
            continue
        try:
            running += parse_amount(cell)
        except AmountError:
            # A non blank cell that cannot be read means the sum can never be
            # verified. That is inconclusive, not evidence that the heading is an
            # ordinary account.
            return None
        seen_detail = True
    return seen_detail and running == amount


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
        row_section = section
        if heading is not None:
            if not cell:
                section = heading
                continue
            try:
                amount = parse_amount(cell, f"{path} line {number}")
            except AmountError:
                skipped.append(f"line {number}: {label!r} has no readable amount in {column_name}")
                section = heading
                continue
            carries_total = _heading_carries_section_total(rows[number:], index, amount)
            if carries_total is not False:
                # True: this export prints the section total on the heading row
                # itself, so the amount is recorded as a total rather than becoming an
                # account that double counts everything beneath it. None: a detail
                # cell beneath is unreadable, so the sum cannot be checked either way.
                # The conservative reading is the same for both: keep the heading as a
                # section total. It stays visible as a suggested exclusion in the
                # mapping file, and the rows beneath keep the right section, whereas
                # reading it as an account on unverifiable evidence would hold the
                # previous section open and misfile every row under this heading.
                section = heading
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
            # Every detail cell beneath parses and the sum does not match, so this is
            # an ordinary account that happens to be named like a section heading, for
            # example a single "Cost of Goods Sold" line in a flat export. It falls
            # through to be read as a normal account row carrying the section its own
            # label names, while the rows after it keep the enclosing section.
            row_section = heading

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
                section=row_section,
                is_total=is_total_row(label),
            )
        )

    if not parsed:
        raise PnlError(
            f"{path}: no account rows found. If this is a two column file, give it an "
            f"'account,amount' header row."
        )
    return PnlFile(rows=tuple(parsed), layout="report", amount_column=column_name, skipped=tuple(skipped))
