# Changelog

All notable user-visible changes are documented here. The project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.0] - 2026-08-30

### Added

- `set-alternate` command for replacing every matching Separation's composite
  fallback with one linear DeviceCMYK FunctionType 2 preview while preserving
  the spot plate and all paint operands.
- A real veraPDF public-corpus case and synthetic before/after render for
  alternate-preview changes.
- A reproducible synthetic 64/128-spot inventory benchmark with deterministic
  page/Form parse counts, timings, and Python heap measurements.

### Changed

- Read-only inventory now attributes structural hazards in one traversal and
  interprets every reached page or compatible Form stream once, eliminating the
  previous colorant-by-stream parse multiplier while preserving per-colorant
  status and paint counters.

### Fixed

- Shared Forms with direct resource dictionaries now use stable owner-bound
  identities instead of transient pikepdf wrapper IDs, and compatible shared
  Form use is attributed to every calling page without recounting its paint.
- Cached changes from nested shared Forms now propagate through every enclosing
  Form, so removal reports every affected calling page even when the inner Form
  was first reached through another path.
- Inline images are recognized with current pikepdf objects. Page aliases and
  Form streams that would require rewriting now fail during the dry run rather
  than reaching an unsafe apply pass.
- Page-tree `/Parent` back-links and shared resource-hazard subtrees are handled
  without repeated cross-expansion, keeping inventory linear as pages, Forms,
  and aliases grow while preserving non-page `/Parent` reachability.

### Safety

- Alternate-preview changes reject ambiguous roles, reserved/process names,
  target-related DeviceN/NChannel use, malformed target name fields, signed or
  restricted inputs, and unsafe output aliases before publication.
- In-memory and post-save checks bind inventory, content streams, the complete
  document graph, and every requested preview definition to the planned change.
- Release metadata validation now binds each version's comparison link to the
  immediately preceding dated release, and the maintainer checklist names every
  local lock, metadata, size, corpus, benchmark, and artifact gate.

## [0.3.0] - 2026-08-30

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
- A pinned six-file public prepress release corpus spanning painted
  Separations, DeviceCMYK and DeviceRGB alternates, DeviceN dependencies,
  arbitrary NChannel process components, and multi-page definitions.
- Reproducible synthetic removal and rename documentation images.
- Automated GitHub release assets, SHA-256 checksums, and build-provenance
  attestations after the full test and public-corpus gates pass.

### Fixed

- Rename now accepts pikepdf's storage-only XMP packet reserialization in valid
  PDF/A files while still rejecting RDF values, namespaces, comments, packet
  identity/mutability, malformed wrapper grammar, and unrelated metadata
  changes.

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
- The release build backend is exactly pinned and locked; unexpected release
  filenames, symlinks, extra assets, version drift, and checksum changes fail
  before publication.

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

[Unreleased]: https://github.com/Hyperrick/spotpdf/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/Hyperrick/spotpdf/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Hyperrick/spotpdf/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/Hyperrick/spotpdf/releases/tag/v0.2.1
