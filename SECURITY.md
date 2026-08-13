# Security policy

## Supported versions

Security fixes are applied to the latest version on the default branch.

## Reporting a vulnerability

Please use this repository's private vulnerability reporting feature. Do not open a
public issue for a suspected security vulnerability. Include a clear description,
reproduction steps, impact, and any suggested mitigation.

A valid report will be acknowledged within seven days, and the fix and disclosure
timeline will be agreed with the reporter.

## What this tool does and does not do

It reads two CSV files, writes a CSV or JSON file, and prints a report. It makes no
network call at any point, holds no credentials, and has no runtime dependencies
outside the Python standard library. The benchmark data is shipped inside the package
and is never fetched.

## Local path trust boundary

This is a single user command line tool, not a sandbox or a service. Its input and
output paths are chosen by the operating system user who runs it and may refer to any
file that user can read or write. Do not run it with elevated privileges, and do not
pass paths supplied by a less trusted user, a web request or another tenant without
first enforcing a caller specific safe root.

`map` refuses to overwrite an existing output file unless `--force` is given, so a
reviewed mapping is not lost to a repeated command.

## Untrusted ledger content

Account names come from whatever the ledger holds and are written into a CSV that is
meant to be opened in a spreadsheet. Values written by this tool are escaped against
formula injection: a leading `=` or `@` is always escaped, and a leading `+` or `-`
is escaped unless what follows is a plain number, so a ledger code such as `-00123`
survives and still joins back to the ledger.

Amounts are parsed strictly. `NaN` and `Infinity` are refused at the door, because
`Decimal` accepts both and then raises on the first comparison.
