## Summary

Describe the narrowly scoped change and why it is needed.

## Verification

- [ ] Tests cover the changed behavior.
- [ ] `uv run ruff check .` passes.
- [ ] `uv run ruff format --check .` passes.
- [ ] `uv run python -m unittest discover -s tests -v` passes.
- [ ] Documentation and `CHANGELOG.md` are updated when behavior is user-visible.

## PDF and compatibility safety

- [ ] No customer, production, confidential, or personal PDF is included.
- [ ] Every fixture is synthetic, or its source and redistribution license are documented.
- [ ] Unsupported semantics fail without publishing output.
- [ ] CLI output, exit-code, packaging, and compatibility impacts are described.
