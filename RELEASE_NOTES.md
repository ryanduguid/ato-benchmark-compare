# v0.1.5

- add structured dependency metadata for calculation warnings and comparison notes;
- add the public `to_evidenced_dict` serializer so omitted inputs are not presented as
  zero-valued evidence; and
- preserve the existing human-readable warning and note fields for compatibility.

# v0.1.4

Changes since `v0.1.3`:

- read the package version from `atobenchmark/__init__.py` alone, so the module attribute and the distribution metadata cannot drift apart again; and
- add a test asserting `atobenchmark.__version__` equals the installed distribution version.

Version 0.1.3 shipped `__version__ = "0.1.2"`. Callers that quote that attribute beside a computed figure, such as the `engine_version` field on `au-tax-mcp-server` tool responses, understated the engine that produced the number. Nothing about the comparison arithmetic or the bundled benchmark figures changed.

The runtime remains dependency-free. The bundled benchmark figures are derived from Australian Taxation Office data licensed under Creative Commons Attribution 2.5 Australia; see `LICENSE`, `NOTICE` and the source notes in `docs/`.
