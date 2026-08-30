# Changelog

All notable user-visible changes are documented here. The project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.7.0] - 2026-08-30

### Added

- Automated repository hygiene checks reject tracked PDFs and broken local
  documentation file targets, while CI regenerates every synthetic documentation
  image, verifies an exact source-fingerprint manifest and SVG output, and uses
  bounded PNG drift checks before packaging.
- CodeQL analysis for Python runs on pull requests, `main`, a weekly schedule,
  and manual dispatch with read-only contents plus the required code-scanning
  write permission.
- A support-routing guide and structured usage-question form distinguish
  questions, reproducible bugs, feature requests, and private vulnerability
  reports while keeping confidential PDFs and sensitive output data out of
  project channels.
- A tokenless PyPI Trusted Publishing path uploads the already tested wheel and
  source archive only after the corresponding GitHub Release is immutable.
- Release validation now rejects repository-relative or stale-tag README links
  that would break on PyPI. A fail-closed PyPI Markdown gate renders the exact
  long description packaged in both distributions and requires them to match;
  bounded archive parsing rejects resource floods and ambiguous metadata paths.
  `twine check --strict` remains responsible for metadata and warning checks.
- Every mutating CLI command now accepts `--dry-run` instead of an output path.
  The mode executes the complete rewrite, serialization, strict reopen, and
  semantic verification pipeline, then discards the verified temporary PDF.
  Text identifies the dry run; JSON adds `dry_run: true` and omits `output`
  without changing normal mutation result shapes.
- Wheel and source-archive Core Metadata now expose the canonical Support and
  Security channels. The package gate requires each link exactly once in both
  distributions before publication.
- Contributor setup now names Poppler's `pdftoppm` prerequisite, provides
  macOS and Debian/Ubuntu installation commands, adds POSIX and PowerShell
  verification, and distinguishes Poppler-only documentation/rename rendering
  from qpdf/Ghostscript convert-render and release-corpus requirements.
- An actionable troubleshooting guide maps common fail-closed errors to causes
  and safe next steps without treating `--force`, higher budgets, or PDF-object
  patching as ways to bypass unsupported semantics and document restrictions.
- The seven supported Python operations, their core result records, limits,
  colorant roles/kinds, and controlled errors now have one canonical package-root
  import surface. All path arguments consistently accept strings and path-like
  objects, and built distributions declare and verify PEP 561 inline typing.
- Stable-release validation now binds both the bug-report and usage-question
  version placeholders to the package version.

### Security

- Spot removal now plans every target resource-alias deletion only inside a
  proven Page/Form content-resource context. Genuine but uninvoked Forms are
  analyzed across every effective resource scope before selected aliases can be
  removed. Retained color-space dependencies, Form inline images, selected
  targets reachable through non-content trailer roots, private resource
  lookalikes, and resource containers with non-content owners fail closed
  without replacing an existing destination.
- The PyPI publisher is isolated in a two-step, tag-only environment job with
  only `id-token: write`; it cannot check out or rebuild repository code and
  uses no stored package-index credential.
- Read-only CI checkouts no longer retain Git credentials beyond checkout.
- Dry-run success is emitted only after private temporary storage has been
  cleaned up. Handled mutation failures remove the private output; a filesystem
  cleanup failure becomes an I/O error before any success record is emitted.

## [0.6.0] - 2026-08-30

### Added

- Schema-versioned JSON output for all six input-processing commands through
  `--format json`, with one deterministic command-specific result on stdout or
  one classified error on stderr.
- Stable machine error codes for usage, budgets, unsupported spot semantics,
  validation, PDF parsing, I/O, invalid values, invariant failures, and nesting
  limits. Budget failures include the metric, public field, observed value,
  limit, and matching CLI option.
- A normative JSON contract with Enfocus Switch, shell, and CI recipes plus
  subprocess coverage for every command, empty results, Unicode and controls,
  parser failures, native decode failures, and output atomicity.

### Changed

- Invalid command-line arguments now exit with `64`, leaving exit `2`
  exclusively for a successful `check` result whose requested name is present.
- Stable-release validation now also rejects stale development-only claims and
  mismatched `spotpdf_version` examples in the README and normative JSON,
  processing-budget, and security documentation.

### Compatibility

- Human-readable text remains the default; successful command output and
  runtime-error wording remain unchanged. Help now documents `--format`, usage
  errors move from exit `2` to `64`, successful mutations remain exit `0`, and
  `check` remains exit `0` for absent and `2` for present. Help and version
  output remain text in JSON mode.

## [0.5.0] - 2026-08-30

spotpdf 0.5.0 adds fail-closed conversion of one supported named
`/Separation` to explicit DeviceCMYK vector or text paint. The operator supplies
the CMYK recipe; spotpdf never guesses it from the existing alternate preview.

```console
spotpdf convert input.pdf --spot "Varnish" \
  --to-cmyk 0,80,100,0 -o output.pdf
```

### Added

- `convert` CLI command and `convert_spot_to_cmyk` library API for replacing a
  strictly supported named Separation with explicit DeviceCMYK vector/text
  paint from an operator-supplied process recipe.
- Stateful tint conversion across independent fill/stroke state, balanced
  graphics-state scopes, text rendering modes, and compatible nested Forms.
- A synthetic qpdf/Poppler/Ghostscript conversion oracle, generated conversion
  walkthrough, and seventh pinned public-corpus case.
- Immutable public `ProcessingLimits` configuration and five positive-integer
  CLI overrides for input bytes, pages, reachable graph entries, decoded
  page/Form content bytes, and content operators.
- A normative processing-budget guide with exact inclusive counter semantics,
  library examples, default rationale, and isolation boundaries.

### Safety

- Conversion builds and verifies a complete precomputed resource/stream plan,
  requires the target plate to be absent after strict reopen, and fingerprints
  every unplanned document semantic before atomic publication.
- A resource-scope-aware preflight proves that deleting target aliases cannot
  leave nested color spaces, images, shadings, transparency groups, or Tiling
  Pattern content with stale references.
- Explicit process conversion fails closed for target-related DeviceN,
  Type 5 halftones, OPI and other prepress dependencies, images and their
  alternates, patterns, shadings, Type 3 fonts, annotation appearances,
  transparency, effective overprint, effective `/DefaultCMYK`, ambiguous
  resource/Form contexts, and malformed or unsupported paint state.
- Unknown content operators outside PDF `BX`/`EX` compatibility sections are
  rejected before conversion so vendor extensions cannot hide paint or graphics
  state changes from the conversion plan. Standard operators are also checked
  against page/Form versus text-object context, and ExtGState font selections
  participate in the Type 3 refusal.
- A stream-owner preflight refuses page/Form cross-role aliases, non-content
  aliases such as attachments and metadata, and externally referenced
  `/Contents` Array members. Only direct `/XObject` slots in proven Page/Form
  resource contexts authorize Form writes; external StructTree MCR `/Stm`
  associations and private lookalike structures fail closed.
- Every input-processing subcommand now performs one fixed-order source budget
  preflight before analysis or mutation. Any overrun publishes no output,
  preserves an existing `--force` destination, and leaves no created private
  temporary candidate behind.
- Reachable graph entries and content operators are counted incrementally rather
  than materializing complete graph-edge or instruction lists solely for the
  budget check. Application ceilings remain separate from qpdf safeguards and
  external CPU/RAM/time isolation.
- Native qpdf warnings remain collected and fail closed but are no longer
  duplicated ahead of the CLI's single user-facing error line.

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

[Unreleased]: https://github.com/Hyperrick/spotpdf/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/Hyperrick/spotpdf/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/Hyperrick/spotpdf/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Hyperrick/spotpdf/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Hyperrick/spotpdf/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Hyperrick/spotpdf/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/Hyperrick/spotpdf/releases/tag/v0.2.1
