# Architecture

`spotpdf` is deliberately split into small modules with one-way dependencies.

```text
CLI
 └─ document orchestration and atomic publication
     ├─ semantic color-space inventory
     ├─ content resource resolution
     ├─ document safety preflight
     └─ stateful content-stream rewrite
         └─ shared domain models and object identity helpers
```

## Module responsibilities

- `cli.py` owns arguments, exit codes, and human-readable output.
- `document.py` owns strict opening, two-pass processing, temporary output,
  post-save verification, and atomic replacement.
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
  → complete dry run
  → in-memory rewrite
  → in-memory target check
  → save to sibling temporary file
  → strict reopen and target check
  → atomic destination replacement
```

Any exception before the last step removes the temporary file. A pre-existing
destination remains untouched, including when `--force` was requested.

## Why dry-run and apply are separate

PDF semantics can depend on inherited graphics state, Form resources, and text
rendering modes. The dry run proves that every selected use is supported before
any in-memory object is changed. This currently costs a second stream parse but
keeps the all-or-nothing guarantee simple and auditable.

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
