"""Command line interface.

Exit codes:
    0  comparison produced and nothing to flag: the key ratio sits inside the ATO
       range, or no published range applies to this turnover
    1  the run could not produce a comparison
    2  comparison produced and the key ratio sits outside the ATO range
    3  comparison produced but accounts are still carrying suggested buckets
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, dataset as dataset_module, mapping as mapping_module, pnl as pnl_module
from .atomic_io import atomic_write_text
from .dataset import Dataset, DatasetError, RATIO_KEYS, RATIO_LABELS
from .mapping import (
    BUCKETS,
    MappingError,
    REVIEW,
)
from .money import AmountError, parse_amount, percent_range
from .ratios import RatioError, compute
from .report import compare as compare_ratios, render_text, to_dict

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_OUTSIDE = 2
EXIT_UNREVIEWED = 3


def _load(args: argparse.Namespace) -> Dataset:
    return dataset_module.load(getattr(args, "year", None))


def _refuse_to_write_over_an_input(out: Path, inputs: list[str | None]) -> None:
    """Stop an output path from destroying a file this run reads.

    A mapping file is hand reviewed work and the profit and loss is the source record.
    Writing either of them is never what the user meant, and it is not recoverable.
    """
    for candidate in inputs:
        if candidate is None:
            continue
        other = Path(candidate)
        try:
            if out.resolve() == other.resolve():
                raise MappingError(
                    f"refusing to write {out} because this run reads that same file"
                )
        except OSError as exc:
            raise MappingError(
                f"refusing to write {out}: cannot resolve {other} ({exc})"
            ) from exc


def cmd_industries(args: argparse.Namespace) -> int:
    data = _load(args)
    matches = data.search(args.search) if args.search else list(data.business_types)
    if not matches:
        print(f"No business type matches {args.search!r}.", file=sys.stderr)
        return EXIT_ERROR
    for business_type in matches:
        key = RATIO_LABELS[business_type.key_ratio]
        print(f"{business_type.name}  [key range: {key}]")
    print(f"\n{len(matches)} of {len(data.business_types)} business types, {data.year} benchmarks")
    return EXIT_OK


def cmd_show(args: argparse.Namespace) -> int:
    data = _load(args)
    business_type = data.get(args.industry)
    print(f"{business_type.name}")
    print(f"Benchmark year: {data.year}")
    print(f"Key range:      {RATIO_LABELS[business_type.key_ratio]}")
    print()
    for band in business_type.bands:
        print(f"  {band.label}")
        for name in RATIO_KEYS:
            benchmark = band.ratios.get(name)
            shown = (
                percent_range(benchmark.minimum, benchmark.maximum) if benchmark else "not published"
            )
            print(f"    {RATIO_LABELS[name]:<30} {shown}")
    print()
    print(f"Source: {data.source.get('resource_url')}")
    return EXIT_OK


def cmd_buckets(args: argparse.Namespace) -> int:
    width = max(len(name) for name in BUCKETS)
    for name, description in BUCKETS.items():
        print(f"{name.ljust(width)}  {description}")
    return EXIT_OK


def cmd_map(args: argparse.Namespace) -> int:
    out = Path(args.out)
    _refuse_to_write_over_an_input(out, [args.profit_and_loss])
    if out.exists() and not args.force:
        print(
            f"{out} already exists. Reviewed mappings are worth keeping, so this will not "
            f"overwrite one. Pass --force if you mean to replace it.",
            file=sys.stderr,
        )
        return EXIT_ERROR

    source = pnl_module.read(Path(args.profit_and_loss), args.amount_column)
    draft = mapping_module.suggest_mapping(source.rows)

    out.parent.mkdir(parents=True, exist_ok=True)
    mapping_module.write_mapping(out, draft.rows)

    print(f"Wrote {out} with {len(draft.rows)} account(s) from a {source.layout} layout export.")
    print(f"Amounts were read from {source.amount_column}.")
    if draft.duplicates:
        # The compare command refuses this export whatever the mapping holds, because
        # route will not answer for a second ledger row of the same name and
        # read_mapping will not accept a second mapping row for it. The export itself
        # has to change, so the message asks for distinct names or one combined row.
        print(
            f"{len(draft.duplicates)} account name(s) appear more than once in the "
            f"export and share one mapping row: {', '.join(draft.duplicates[:5])}"
        )
        print(
            "Give them distinct names in the export, or combine them into one row. "
            "Until then compare will not run against this export."
        )
    if source.skipped:
        print(f"{len(source.skipped)} row(s) carried no readable amount and were left out:")
        for line in source.skipped[:10]:
            print(f"  {line}")
        if len(source.skipped) > 10:
            print(f"  ... and {len(source.skipped) - 10} more")
    if draft.needs_review:
        print(
            f"{draft.needs_review} account(s) are marked {REVIEW} and must be given a bucket."
        )
    print()
    print("Every bucket in that file is a suggestion made from the account name alone.")
    print("Read the ledger, correct the buckets, and change the source column to 'reviewed'.")
    print("Run 'ato-benchmark-compare buckets' for what each bucket means.")
    return EXIT_OK


def cmd_compare(args: argparse.Namespace) -> int:
    data = _load(args)
    business_type = data.get(args.industry)
    source = pnl_module.read(Path(args.profit_and_loss), args.amount_column)
    mapping = mapping_module.read_mapping(Path(args.mapping))
    routing = mapping_module.route(source.rows, mapping, args.flip_expense_signs)

    w1 = parse_amount(args.w1, "--w1") if args.w1 is not None else None
    figures = compute(routing.totals, w1=w1)
    comparison = compare_ratios(data, business_type, figures)
    comparison.notes.extend(routing.notes)

    if args.json and args.json != "-":
        _refuse_to_write_over_an_input(Path(args.json), [args.profit_and_loss, args.mapping])
    if args.json:
        payload = to_dict(comparison, unreviewed=routing.unreviewed)
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        if args.json == "-":
            print(text, end="")
        else:
            atomic_write_text(Path(args.json), text, encoding="utf-8", newline="\n")
            print(f"Wrote {args.json}")
    if args.json != "-":
        print(render_text(comparison, unreviewed=routing.unreviewed))

    if routing.unreviewed and not args.accept_unreviewed:
        return EXIT_UNREVIEWED
    if comparison.outside_key_range:
        return EXIT_OUTSIDE
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ato-benchmark-compare",
        description=(
            "Compare a set of profit and loss figures against the ATO small business "
            "benchmarks. Nothing leaves this machine."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_year(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--year",
            help=f"benchmark year. Available: {', '.join(dataset_module.available_years())}",
        )

    industries = subparsers.add_parser("industries", help="list the ATO business types")
    industries.add_argument("--search", help="only show business types containing this text")
    add_year(industries)
    industries.set_defaults(func=cmd_industries)

    show = subparsers.add_parser("show", help="show the published ranges for one business type")
    show.add_argument("industry")
    add_year(show)
    show.set_defaults(func=cmd_show)

    buckets = subparsers.add_parser("buckets", help="explain the mapping buckets")
    buckets.set_defaults(func=cmd_buckets)

    mapper = subparsers.add_parser(
        "map", help="write a reviewable account mapping from a profit and loss export"
    )
    mapper.add_argument("--profit-and-loss", required=True)
    mapper.add_argument("--out", required=True)
    mapper.add_argument("--amount-column", help="column number or heading holding the amounts")
    mapper.add_argument("--force", action="store_true", help="overwrite an existing mapping file")
    mapper.set_defaults(func=cmd_map)

    comparer = subparsers.add_parser("compare", help="produce the benchmark comparison")
    comparer.add_argument("--profit-and-loss", required=True)
    comparer.add_argument("--mapping", required=True)
    comparer.add_argument("--industry", required=True)
    comparer.add_argument("--amount-column", help="column number or heading holding the amounts")
    comparer.add_argument("--w1", help="activity statement W1 total for the year")
    comparer.add_argument("--json", help="write the result as JSON to this path, or - for stdout")
    comparer.add_argument(
        "--flip-expense-signs",
        action="store_true",
        help="negate expense amounts, for exports that show expenses as negatives",
    )
    comparer.add_argument(
        "--accept-unreviewed",
        action="store_true",
        help="exit 0 even though accounts still carry suggested buckets",
    )
    add_year(comparer)
    comparer.set_defaults(func=cmd_compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    # On Windows a redirected stdout uses the machine's ANSI codepage, not UTF-8, so an
    # account name carrying a character outside that codepage would end the run with a
    # UnicodeEncodeError after the work was already done.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="backslashreplace")
            except (ValueError, OSError):
                pass

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (DatasetError, MappingError, pnl_module.PnlError, RatioError, AmountError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
