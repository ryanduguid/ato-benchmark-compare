"""Structured dependencies for notes and checks emitted by the engine."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceMessage:
    """Human-readable engine message and the facts needed to state it."""

    code: str
    text: str
    required_fields: frozenset[str]
