# Architecture

`spotpdf` is deliberately split into small modules with one-way dependencies.

```text
CLI
 └─ document orchestration and atomic publication
     ├─ semantic color-space inventory
     ├─ complete spot-rename planning and application
     ├─ content resource resolution
     ├─ document safety preflight
     └─ stateful content-stream rewrite
         └─ shared domain models and object identity helpers
```

## Module responsibilities

- `cli.py` owns arguments, exit codes, and human-readable output.
- `document.py` owns inspect/check/remove orchestration and two-pass removal.
- `publication.py` owns strict opening, compatibility-preserving saves,
  temporary output, and atomic/no-clobber publication shared by mutating
  commands.
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
- `objects.py` owns stable identity and cycle-safe graph traversal support.
- `model.py` contains shared values, result types, and user-facing exceptions.

## Mutation pipeline

The output path is never opened as the working document.

```text
strict open
  → declaration inventory
  → mutation preflight
  → complete rename plan or removal dry run
  → in-memory rewrite
  → in-memory semantic check
  → save to sibling temporary file
  → strict reopen and semantic verification
  → atomic destination replacement
```

Any exception before the last step removes the temporary file. A pre-existing
destination remains untouched, including when `--force` was requested.

## Why planning and apply are separate

Removal semantics can depend on inherited graphics state, Form resources, and
text rendering modes, so removal performs a full dry run. Rename first builds a
deduplicated plan containing every definition slot and supported exact-name
reference. In both cases the plan is known to be complete before any in-memory
object is changed. Removal currently pays for a second stream parse; rename does
not interpret or rewrite paint operands. It does parse page and Form content
syntax after saving and compares their location-bound decoded stream hashes.

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

Decoded streams remain byte-sensitive. The only storage normalization is for a
valid `/Type /Metadata` plus `/Subtype /XML` stream: the XML root, comments,
processing instructions, scoped namespace bindings, and meaningful XMP packet
fields are fingerprinted semantically. Invalid XML, DTD-bearing XML, malformed
packet wrappers, and XML above the small canonicalization guard are compared as
decoded raw bytes instead.

## Resource limits

General object inventory uses iterative, root-context-aware traversal with cycle
tracking and cached graph edges. Indirect definitions retain their PDF
object/generation number; direct definitions use a deterministic reachable path
as their identity. Form invocation and resource nesting are limited to 64 levels
and fail with a normal user-facing error beyond that boundary. Operators with
unresolved color spaces, patterns, XObjects, or shadings are rejected when
encountered during a removal pass.

The tool does not impose whole-process CPU or memory quotas. Use an external
sandbox for hostile PDFs as described in [SECURITY.md](../SECURITY.md).
