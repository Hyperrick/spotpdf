# Changelog

All notable user-visible changes are documented here. The project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Atomic `rename` command for exact, case-sensitive spot-plate aliasing without
  changing alternate color spaces, tint transforms, tint operands, resource
  aliases, or content streams.
- Rename support for structurally consistent DeviceN/NChannel spot components,
  `/Colorants`, `/MixingHints`, page `/SeparationInfo`, and normal PrinterMark
  appearances.
- Role-aware DeviceN/NChannel inventory for arbitrary process component names,
  canonical CMYK components, spot components, `/All`, and `/None`.
- Stable object identities and human-readable locations for every reachable
  Separation and DeviceN definition.
- Exact-name dependency inventory for NChannel process/colorant/mixing-hint
  entries, page `/SeparationInfo`, printer-mark colorants, and TrapNet
  `/SeparationColorNames`.

### Safety

- Rename rejects missing or ambiguous sources, existing destinations, process
  and reserved names, malformed name relationships, and unsupported TrapNet,
  type 5 halftone, OPI, and PrinterMark rollover/down dependencies before any
  output is published.
- Rename output is reopened and semantically verified before atomic replacement;
  failed `--force` processing preserves the existing destination.
- Rename verification binds alternate spaces, tint transforms, content streams,
  and dependency payloads to their semantic locations and rejects any change
  beyond the planned name slots.
- Target-related DeviceN/NChannel, Process, MixingHints, Colorants, and
  SeparationInfo relationships are validated before mutation.
- `remove --all` no longer treats custom-named NChannel process components as
  spots.
- Removal fails closed when a selected name remains in a supported exact-name
  prepress dependency, including pre-separated page metadata.

## [0.2.1] - 2026-08-29

First public beta release.

### Added

- `list`, `check`, exact-name `remove`, and atomic `remove --all` commands.
- Removal of supported vector fills, strokes, combined paint, and text paint.
- Nested Form processing with context and nesting safeguards.
- A synthetic demo generator, before/after screenshots, and runtime-generated
  test PDFs.

### Safety

- Preserve process `/Cyan`, `/Magenta`, `/Yellow`, and `/Black` in `--all`.
- Preserve reserved `/All` and `/None` separations.
- Refuse signed, encrypted, restricted, malformed, and unsupported mutations.
- Refuse unresolved color-space and pattern resources.
- Reject output symlinks instead of following them under `--force`.
- Traverse deep PDF object graphs iteratively and reject excessive Form nesting
  without a traceback or partial output.
- Verify saved output before atomic replacement and preserve an existing forced
  destination whenever processing fails.

[Unreleased]: https://github.com/Hyperrick/spotpdf/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/Hyperrick/spotpdf/releases/tag/v0.2.1
