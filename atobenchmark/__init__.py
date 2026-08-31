"""Compare a set of profit and loss figures against the ATO small business benchmarks."""

from __future__ import annotations

from .dataset import load
from .money import parse_amount
from .report import compare, to_evidenced_dict

__version__ = "0.1.5"

__all__ = ["__version__", "compare", "load", "parse_amount", "to_evidenced_dict"]
