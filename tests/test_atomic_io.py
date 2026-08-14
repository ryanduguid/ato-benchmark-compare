from __future__ import annotations

import os
from pathlib import Path

import pytest

from atobenchmark.atomic_io import atomic_text_writer, atomic_write_text
from atobenchmark.mapping import MappingRow, write_mapping


def _staged_files(path: Path) -> list[Path]:
    return list(path.parent.glob(f".{path.name}.*.tmp"))


def test_failed_json_replacement_keeps_the_reviewed_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "comparison.json"
    output.write_text("old reviewed result\n", encoding="utf-8")

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise PermissionError("destination is locked")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(PermissionError, match="locked"):
        atomic_write_text(output, "new result\n", newline="\n")

    assert output.read_text(encoding="utf-8") == "old reviewed result\n"
    assert _staged_files(output) == []


def test_failed_mapping_generation_never_truncates_the_prior_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "mapping.csv"
    output.write_text("account,bucket\nReviewed,turnover\n", encoding="utf-8")

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("flush failed")

    monkeypatch.setattr(os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="flush failed"):
        write_mapping(
            output,
            [MappingRow("Replacement", "turnover", "suggested")],
        )

    assert output.read_text(encoding="utf-8") == "account,bucket\nReviewed,turnover\n"
    assert _staged_files(output) == []


def test_exception_inside_staged_writer_leaves_no_partial_output(tmp_path: Path) -> None:
    output = tmp_path / "mapping.csv"

    with pytest.raises(RuntimeError, match="generation stopped"):
        with atomic_text_writer(output, newline="") as handle:
            handle.write("partial")
            raise RuntimeError("generation stopped")

    assert not output.exists()
    assert _staged_files(output) == []
