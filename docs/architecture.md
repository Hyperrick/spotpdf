# Architecture

`spotpdf` is deliberately split into small modules with one-way dependencies.

```text
CLI
 └─ document orchestration and atomic publication
     ├─ semantic color-space inventory
     ├─ complete rename, alternate-preview, and conversion planning
     ├─ content resource resolution
     ├─ document safety preflight
     └─ stateful content-stream rewrite
         └─ shared domain models and object identity helpers
```

## Module responsibilities

- `cli.py` owns command routing and maps completed operations to exit codes;
  `cli_parser.py` owns argument definitions, format selection, and the distinct
  usage-error exit policy; `cli_output.py` owns explicit v1 JSON serializers,
  stable error classification, and backward-compatible text rendering;
  `cli_limits.py` owns the shared positive-integer budget options; and
  `cli_dry_run.py` owns private, automatically discarded destinations for
  fully serialized and verified mutation dry runs.
- `limits.py` owns immutable public processing configuration, metric metadata,
  and the structured budget-exceeded error.
- `budget_preflight.py` owns the fixed-order source audit and usage record;
  `budget_graph.py` and `budget_content.py` perform incremental graph and
  page/Form content accounting.
- `document.py` owns inspect/check/remove orchestration and two-pass removal.
- `inspection.py` combines semantic declarations, structural hazards, and
  content usage into the public read-only report.
- `inventory_hazards.py` attributes each colorant's first removal-preflight
  hazard while traversing page resources once.
- `inventory_content.py` interprets each reached page or compatible Form stream
  once and accumulates per-colorant pages, paint operations, and unsupported
  contexts.
- `inventory_usage.py` contains private usage and deterministic work counters.
- `publication.py` owns strict opening, compatibility-preserving saves,
  temporary output, and atomic/no-clobber publication shared by mutating
  commands.
- `alternate.py` owns alternate-preview mutation orchestration and
  in-memory/post-save verification.
- `alternate_plan.py` discovers every matching Separation, rejects target-related
  DeviceN/malformed use, and owns exact preview-slot application.
- `alternate_validation.py` validates existing alternate spaces/tint functions
  and rejects target definitions embedded in inline-image content dictionaries.
- `cmyk.py` owns exact percentage validation, canonical PDF-number storage, and
  tint-by-recipe arithmetic shared by preview changes and conversion.
- `convert.py` owns explicit-DeviceCMYK orchestration, atomic publication, and
  in-memory/post-save verification; `separation_targets.py` validates one
  unambiguous exact target and proves inventory coverage.
- `convert_plan.py` combines precomputed resource and content edits into one
  complete plan; `convert_resources.py` plans exact target-alias deletions,
  while `convert_aliases.py` rejects surviving resource-scoped color-space,
  image, shading, transparency-group, and Tiling Pattern references.
- `convert_resource_contexts.py` records only resource dictionaries proven to
  belong to actual Page or Form content contexts, retains every reachable path
  to each context, and records indirect ancestor containers for owner
  provenance.
- `convert_streams.py` traverses page/Form invocation and resource contexts;
  `convert_content.py` plans stateful operator replacement,
  `convert_operators.py` owns the ISO operator whitelist and graphics-object
  context rules, and `convert_state.py` owns independent fill/stroke state.
  `convert_stream_owners.py` proves that every planned write has only its
  allowed direct content-owner roles.
- `mutation_verification.py` owns inventory/content fingerprints and saved
  content-stream parse checks shared by non-removal mutations.
- `rename.py` owns rename orchestration, location-bound fingerprints, reverse
  planning, and post-save semantic verification.
- `rename_request.py` validates source/destination names and inventory roles.
- `rename_hazards.py` detects unsupported target-related prepress structures.
- `rename_structures.py` validates required target-related DeviceN/NChannel,
  Process, MixingHints, Colorants, and SeparationInfo relationships.
- `rename_plan.py` discovers every supported definition/dependency slot and
  proves inventory coverage before mutation.
- `rename_slots.py` owns physical name application, invariants, exact-slot
  normalization, and semantic fingerprints for planned contexts and the full
  trailer-reachable document graph.
- `metadata_fingerprint.py` owns fail-closed XML Metadata comparison, including
  XMP packet grammar, scoped namespaces, and canonicalized XML meaning.
- `inventory.py` assembles the role-aware Separation/DeviceN/NChannel report.
- `inventory_graph.py` owns iterative reachable-object traversal and location
  propagation.
- `trailer_semantics.py` defines the shared semantic trailer boundary used by
  whole-document fingerprints and conversion owner checks. It excludes only
  storage bookkeeping (`/ID`, `/Prev`, `/Size`, and `/XRefStm`).
- `inventory_prepress.py` inventories supported page and annotation prepress
  dependencies.
- `inventory_values.py` owns small PDF value decoders, path encoding, and
  colorant role rules.
- `colors.py` owns PDF Name decoding, content color-space lookup, syntactic
  color-space parsing, and safe resource cleanup.
- `scan.py` owns document-level mutation restrictions and unsupported-construct
  preflight.
- `content.py` interprets the graphics-state subset needed to remove supported
  path and text paint.
- `content_support.py` owns graphics-state and parsing helpers shared by the
  read-only inventory, removal rewriter, and conversion planner.
- `objects.py` owns stable identity and cycle-safe graph traversal support.
- `model.py` contains shared values, result types, and user-facing exceptions.

## CLI output boundary

The CLI does not serialize domain dataclasses generically. Each command has an
explicit JSON serializer so new internal fields, sets, Enums, or object
identities cannot leak into the public wire format. PDF-controlled strings are
kept semantically exact and escaped by the JSON encoder. Derived arrays and
keys are sorted for deterministic logs, but key order and whitespace are not
part of the API.

Format selection is parsed before command execution so a handled failure uses
the requested stream contract. A completed command writes one JSON record to
stdout; a handled runtime or usage failure writes one to stderr. Parser usage
errors return `64`, which leaves `2` exclusively for the successful
`check`-present predicate. Help and version retain argparse's text behavior.
The versioned contract and evolution rules are normative in
[json-output.md](json-output.md).

## Mutation pipeline

The output path is never opened as the working document.

```text
input-byte check
  → strict open without recovery
  → reject open-time warnings
  → page/graph/content/operator budget preflight
  → qpdf syntax check and final warnings
  → declaration inventory
  → mutation preflight
  → complete rename/alternate/conversion plan or internal removal dry run
  → in-memory rewrite
  → in-memory semantic check
  → save to sibling temporary file
  → strict reopen and semantic verification
  → atomic destination replacement
```

Any private temporary file created for processing is removed after an exception.
A pre-existing destination remains untouched, including when `--force` was
requested.

## Why planning and apply are separate

Removal semantics can depend on inherited graphics state, Form resources, and
text rendering modes, so removal performs a full internal dry run. Rename first
builds a deduplicated plan containing every definition slot and supported
exact-name reference. `set-alternate` builds a deduplicated one-to-one plan for
every target Separation and rejects target-related DeviceN use. Conversion
builds exact target-resource deletions and stateful page/Form stream
replacements. In every case the plan is known to be complete before any
in-memory object is changed.
Removal currently pays for a second stream parse; rename and alternate-preview
changes do not interpret or rewrite paint operands. They parse page and Form
content syntax after saving and compare location-bound decoded stream hashes.

For dictionary-key references such as `/Colorants`, `/Solidities`, and
`/DotGain`, planning also checks the destination key before deleting the source
key. Shared indirect objects are mutated once even when the inventory records
several human-readable contexts. After application, rename builds the inverse
plan (`new` to `old`) without applying it and requires its normalized definition,
dependency-value, and location fingerprint to match the original plan.
The whole-document guard masks only the exact slots in that plan while comparing
the pre- and post-apply graph. A second exact post-apply fingerprint includes
catalog, page tree, document information, and other semantic trailer entries and
must still match after the temporary output is reopened.

Alternate-preview planning uses the same whole-document guard but masks only
members 2 and 3 of each planned Separation array. Apply replaces those members
with `/DeviceCMYK` and one shared indirect FunctionType 2 dictionary. The spot
name, Separation array identity, tint operands, resources, and content streams
remain untouched. Inventory coverage, the requested function, and full document
semantics are checked in memory and again after strict reopen.

Conversion verifies every original target alias and decoded stream before
applying either class of edit. Its normalized whole-document guard masks only
the planned resource slots and content digests, using location-specific
replacement markers so swaps or omissions cannot compare equal. After apply,
the target must be absent, every planned slot must have the exact intended
state, all content must parse, and the unmasked semantic fingerprint must remain
stable across save and strict reopen.

Before those fingerprints are masked, conversion proves the owner roles of
every planned stream. A Page write may have only Page `/Contents` owners; reuse
as a Form or any non-content payload is rejected because those roles can have
different effective resources and semantics. A Form write is accepted only
through a direct `/XObject` slot in resources proven to belong to an actual
Page/Form context. Any additional external owner, including a catalog
`/StructTreeRoot /K` MCR `/Stm` association, fails closed. Appearance and
soft-mask streams remain outside the supported conversion subset.
Resource-looking keys inside `/PieceInfo` or private data, and matching ancestor
path fragments, do not establish a content owner.

A page `/Contents` Array is parsed as one logical operator sequence. When that
sequence changes, conversion keeps the existing Array object, serializes the
complete replacement into its first stream, and empties the remaining streams.
This preserves cross-stream operand/operator semantics without replacing the
page's resource or Contents containers. If any member stream has another
reachable owner, conversion rejects the input instead of consolidating it.

Decoded streams remain byte-sensitive. The only storage normalization is for a
valid `/Type /Metadata` plus `/Subtype /XML` stream: the XML root, comments,
processing instructions, scoped namespace bindings, and meaningful XMP packet
fields are fingerprinted semantically. Invalid XML, DTD-bearing XML, malformed
packet wrappers, and XML above the small canonicalization guard are compared as
decoded raw bytes instead.

## Resource limits

Each of the seven documented path-based operations (`inspect_pdf`, `check_spot`,
`remove_spot`, `remove_all_spots`, `rename_spot`, `set_alternate_cmyk`, and
`convert_spot_to_cmyk`) receives immutable `ProcessingLimits`; no counters or
overrides live in mutable CLI/module globals. File size is checked before
`pikepdf.open()`. After a non-recovering open and rejection of immediate
warnings, one fresh source audit
checks pages, trailer-reachable graph entries, decoded page/Form content, and
lexical content operators in that order. Only then does qpdf's complete syntax
check run and final warnings get rejected. The limits are inclusive and an
overrun occurs on the first value above the configured boundary. Saved-output
verification is not charged against the source a second time, so the contract
does not vary with the fixed number of dry-run, apply, or verification passes.
Exact semantics are normative in
[processing-budgets.md](processing-budgets.md).

Budget graph traversal is iterative and charges each Array item or
Dictionary/Stream value before following it. Shared targets are expanded once
while alias entries still consume work; a large container is never converted
into a separate complete edge tuple for this audit. Decoded byte counting uses
native qpdf buffers without an additional Python `bytes` copy. Operator
accounting uses pikepdf's streaming token filter and discards its output rather
than building a complete instruction list just to enforce the limit.

The processing budgets coexist with the fixed Form invocation/resource nesting
limit of 64. General semantic inventory separately uses iterative,
root-context-aware traversal with cycle tracking and cached graph edges.
Indirect definitions retain their PDF object/generation number; direct
definitions use a deterministic reachable path as their identity. Operators
with unresolved color spaces, patterns, XObjects, or shadings are rejected when
encountered during a removal or conversion pass.

Read-only usage inventory performs one ordered structural-hazard traversal and
one content interpretation pass. It keeps independent per-colorant state, so an
unsupported use freezes only that colorant while unrelated colors continue to
be counted. A compatible shared Form is parsed and counted once, but its cached
effect is attributed to every calling page. Stable owner-anchored identities are
used for direct Form resource dictionaries rather than transient Python wrapper
IDs. The deterministic 64/128-spot contract and representative measurements are
documented in [performance.md](performance.md).

The semantic graph omits only redundant `/Parent` back-links on objects whose
`/Type` is `/Page` or `/Pages`; identically named edges in private and other PDF
structures remain reachable. Resource-hazard subtree results are cached by
stable PDF object identity. Regression fixtures grow pages/Forms and shared
resource aliases from 64 to 128 objects to guard both paths against quadratic
cross-expansion.

Application accounting begins only after qpdf has parsed enough native
structure to expose the graph. One stream's supported non-lossy content filters
are decoded before its length can be observed, and the later syntax check may
decode non-content streams excluded from the page/Form byte budget. Therefore
the tool does not impose whole-process CPU, memory, time, or temporary-output
quotas. qpdf's own parser/filter limits are another independent layer. Use
external process isolation for hostile PDFs as described in
[SECURITY.md](../SECURITY.md).
