# Contributing

This tool compares profit and loss figures against the ATO small business benchmarks
and shows which accounts produced each figure. A person reads the result and decides
what it means. Keep that boundary: nothing here should present a comparison as a
conclusion about whether a return is right, and nothing should lodge, submit or
transmit anything.

## Data boundary

- Use invented data. The `.gitignore` blocks the file names a real ledger arrives
  under, including `pnl*.csv`, `mapping*.csv`, `client-data/` and spreadsheet files.
  Put new fixtures in `examples/` or `tests/fixtures/`. Those two directories take a
  `.xlsx` at their top level. A `.xls`, a `.xlsm`, or any spreadsheet in a
  subdirectory stays blocked, so add the matching `.gitignore` exception in the same
  change that adds the fixture.
- No client, taxpayer or employee data, no ABNs tied to a real business, no
  screenshots of a live ledger, no credentials.
- Check what a fixture implies as well as what it says. An invented profit and loss
  with a real client's account list is still that client's account list.

## ATO rules and figures

- Trace every rule to a primary source and cite it in the pull request. The rules
  currently implemented are recorded in `docs/ato-source-notes-2026-08-13.md` with
  the page and QC number each came from.
- Do not hand edit anything under `atobenchmark/data`. Rebuild it with
  `tools/build_dataset.py` from the ATO workbook so the SHA-256 in the file still
  matches the file it came from.
- When the ATO publishes a new benchmark year, add it as a new dataset file rather
  than replacing an existing one. A comparison run last year should still reproduce.
- Cross check at least one industry against the ATO's own industry page before
  proposing a new dataset year. `tests/test_dataset.py` does this for bakeries.

## Local verification

Python 3.10 or newer. The runtime imports nothing outside the standard library.
`uv` manages the development environment and the lock file is committed.

```bash
uv sync --locked --extra dev --python 3.12
uv run --locked --extra dev --python 3.12 pytest
uv run --locked --extra dev --python 3.12 python -m build
uv run --locked --extra dev --with "pip-audit==2.10.1" pip-audit --local --strict
```

CI repeats this on Ubuntu with Python 3.10 and 3.13, and on Windows with 3.12. Keep
runtime strings ASCII: on Windows, redirected stdout uses the machine's ANSI codepage
rather than UTF-8.

## Pull requests

- Name the rule you changed and the test that pins it. Run that test against the old
  code first. If it passes there too, it is not testing your change.
- When you change a rule, search for everything else that states it. The README
  table, a docstring, the `buckets` command output and a warning string can all keep
  asserting the old rule long after the code has moved.
- Amounts stay in `Decimal` end to end. A float anywhere in the ratio path will be
  rejected: 0.31 as a float is not 0.31, and these comparisons are against published
  figures at two decimal places.
- A displayed figure and its verdict must never disagree. 30.96% is below a 31% floor
  and must not print as 31%.

For a suspected security vulnerability, follow [SECURITY.md](SECURITY.md) rather than
opening an issue.
