"""Recoverable local text-file publication.

Outputs in this package are review artefacts.  Build each one beside its
destination, flush the complete bytes, then replace the destination in one
filesystem operation so a failed rewrite cannot truncate the reviewed file.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO


@contextmanager
def atomic_text_writer(
    path: Path,
    *,
    encoding: str = "utf-8",
    newline: str | None = None,
) -> Iterator[TextIO]:
    """Yield a staged writer and replace ``path`` only after a durable flush."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary)
    replaced = False
    try:
        with os.fdopen(descriptor, "w", encoding=encoding, newline=newline) as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        replaced = True
    finally:
        if not replaced:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def atomic_write_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    newline: str | None = None,
) -> None:
    """Write a complete text value through :func:`atomic_text_writer`."""
    with atomic_text_writer(path, encoding=encoding, newline=newline) as handle:
        handle.write(text)
