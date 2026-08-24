# v0.1.3

Changes since `v0.1.1`:

- align the labour benchmark variance with the ATO method and keep single-line COGS accounts mappable;
- refuse malformed benchmark dataset entries and drive the show command from the published ratio keys;
- preserve mapping identity after CSV input guarding;
- fail closed on overwrite-guard errors and adopt the shared release-policy workflow;
- restore MIT licence detection by moving the data notice to `NOTICE`;
- publish the attested distribution to PyPI via trusted publishing on release;
- add editorconfig, CODEOWNERS, mailmap, job timeouts and Dependabot pacing; and
- refresh documentation so every claim matches the repository, including the repository name.

The runtime remains dependency-free. The bundled benchmark figures are derived from Australian Taxation Office data licensed under Creative Commons Attribution 2.5 Australia; see `LICENSE`, `NOTICE` and the source notes in `docs/`.
