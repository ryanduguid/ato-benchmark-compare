"""Seeded properties for account identity and mapping round trips."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from hypothesis import example, given, seed, settings, strategies as st

from atobenchmark.mapping import BUCKETS, MappingRow, account_key, read_mapping, write_mapping


PROPERTY_SETTINGS = settings(max_examples=100, database=None, deadline=None)


@st.composite
def equivalent_accounts(draw: st.DrawFn) -> tuple[str, str]:
    words = draw(
        st.lists(
            st.from_regex(r"[A-Za-z][A-Za-z0-9&'-]{0,15}", fullmatch=True),
            min_size=1,
            max_size=4,
        )
    )
    canonical = " ".join(word.casefold() for word in words)
    cased = draw(st.sampled_from([canonical, canonical.upper(), canonical.title()]))
    separator = draw(st.sampled_from([" ", "  ", "\t", "\n", "\r\n"]))
    variant = separator.join(cased.split(" "))
    left = draw(st.sampled_from(["", " ", "\t", "\n"]))
    right = draw(st.sampled_from(["", " ", "\t", "\r\n"]))
    return canonical, left + variant + right


@seed(0xA70)
@PROPERTY_SETTINGS
@example(("motor vehicle", "  MOTOR\tVEHICLE\r\n"))
@example(("strasse", "StraSSE"))
@given(equivalent_accounts())
def test_case_and_whitespace_variants_keep_one_logical_identity(
    pair: tuple[str, str],
) -> None:
    """Removing case folding, trimming or whitespace collapse must break this."""
    canonical, variant = pair
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    assert account_key(canonical) == expected
    assert account_key(variant) == expected


@dataclass(frozen=True)
class RowSpec:
    row: MappingRow
    identity: str


@st.composite
def mapping_row(draw: st.DrawFn) -> RowSpec:
    words = draw(
        st.lists(
            st.from_regex(r"[a-z][a-z0-9&'-]{0,12}", fullmatch=True),
            min_size=1,
            max_size=3,
        )
    )
    name = " ".join(words)
    prefix, normalised_prefix = draw(
        st.sampled_from(
            [
                ("", ""),
                ("=", "="),
                ("@", "@"),
                ("+ref ", "+ref "),
                ("-ref ", "-ref "),
                ("\t=", "="),
            ]
        )
    )
    account = prefix + name
    identity = normalised_prefix + name
    row = MappingRow(
        account=account,
        bucket=draw(st.sampled_from(sorted(BUCKETS))),
        source=draw(st.sampled_from(["reviewed", "suggested"])),
        note=draw(st.sampled_from(["", "checked", "invented fixture"])),
        amount=draw(st.sampled_from(["", "0", "1.25", "-50"])),
    )
    return RowSpec(row=row, identity=identity)


@seed(0xA701D)
@PROPERTY_SETTINGS
@given(
    specs=st.lists(
        mapping_row(), min_size=1, max_size=8, unique_by=lambda spec: spec.identity
    )
)
def test_generated_reviewed_mappings_round_trip_without_identity_loss(
    specs: list[RowSpec],
) -> None:
    """Dropping CSV guarding, keyed identity or a reviewed field must break this."""
    rows = [spec.row for spec in specs]
    expected = {
        hashlib.sha256(spec.identity.encode("utf-8")).hexdigest(): spec.row for spec in specs
    }

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "mapping.csv"
        write_mapping(path, rows)

        assert read_mapping(path) == expected
