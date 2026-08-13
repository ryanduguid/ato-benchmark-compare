"""Guard values written to CSV against spreadsheet formula injection.

An account name is attacker controlled in the sense that it comes from whatever the
ledger holds, and the mapping file this tool writes is meant to be opened in Excel.

The guard is deliberately narrower than "anything that could start a formula". A
leading = or @ is always escaped. A leading + or - is escaped only when what follows
is not a plain number, so a general ledger code like -00123 survives the round trip
and still joins back to the ledger, while +A1 does not.
"""

from __future__ import annotations

import re

ALWAYS = ("=", "@")
CONDITIONAL = ("+", "-")

_NUMBER_RE = re.compile(r"^[\d,]*\.?\d+$")


def guard(value: object) -> str:
    text = "" if value is None else str(value)
    if not text:
        return text
    first = text[0]
    if first in ALWAYS:
        return "'" + text
    if first in CONDITIONAL and not _NUMBER_RE.match(text[1:]):
        return "'" + text
    # A leading tab, carriage return or newline can push the rest of the value onto a
    # line Excel then reads as a formula, so treat those as unsafe too.
    if first in "\t\r\n":
        return "'" + text
    return text
