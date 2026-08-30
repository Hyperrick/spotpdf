# JSON output and automation

`spotpdf` can return one deterministic, schema-versioned JSON object for every
input-processing command. This interface is intended for Enfocus Switch,
watched folders, CI pipelines, and other programs that must not parse changing
human-readable prose.

## Invoke JSON mode

Use the global option before the command:

```bash
spotpdf --format json list input.pdf
```

For convenience, the same option is also accepted after a command:

```bash
spotpdf list input.pdf --format json
```

Text remains the default. `--help` and `--version` remain human-readable even
when a format option appears on the command line.

For usage errors, spotpdf selects the last valid, exact `--format text`,
`--format json`, or `--format=...` option before a standalone `--`. This small
pre-parse step lets a trailing `--format json` structure errors in earlier
arguments. Long-option abbreviations such as `--form` are rejected. An invalid
format value cannot itself select JSON.

## Stream contract

One invocation produces at most one JSON document:

- a completed command writes exactly one compact JSON object plus `LF` to
  stdout and leaves stderr empty;
- a handled PDF, I/O, budget, validation, or argument failure writes exactly
  one compact JSON object plus `LF` to stderr and leaves stdout empty when a
  valid `--format json` option occurs before a standalone `--`;
- strings are JSON-escaped, and the emitted byte repertoire is ASCII, which is
  valid UTF-8 and round-trips every Unicode PDF name and path;
- arrays derived from sets are sorted deterministically; and
- no table header, `spotpdf: error:` prefix, usage preamble, warning, or
  traceback surrounds a JSON object.

A normal mutation still creates a PDF output. JSON is the status record, not a
replacement for that output file. A mutation invoked with `--dry-run` never
publishes an output.

The one-record guarantee assumes stdout and stderr remain writable. If a
consumer closes a status pipe, spotpdf returns a transport `io_error` on the
other stream when possible. A mutation may already have atomically published
its verified PDF before that status-write failure; callers should therefore
inspect both the process result and the intended output path.

## Envelope

A successful inventory has this shape:

```json
{
  "command": "list",
  "exit_code": 0,
  "ok": true,
  "result": {
    "colorant_count": 1,
    "colorants": [
      {
        "contexts": ["painted"],
        "kinds": ["Separation"],
        "name": "Varnish",
        "pages": [1],
        "paint_operations": 1,
        "roles": ["spot"]
      }
    ],
    "input": "input.pdf"
  },
  "schema_version": "spotpdf.cli/v1",
  "spotpdf_version": "0.7.1"
}
```

A handled failure has this shape:

```json
{
  "command": "list",
  "error": {
    "code": "budget_exceeded",
    "details": {
      "field": "max_pages",
      "limit": 10000,
      "metric": "pages",
      "observed": 10001,
      "option": "--max-pages"
    },
    "message": "processing budget exceeded: pages 10001 > 10000 (...)"
  },
  "exit_code": 1,
  "ok": false,
  "schema_version": "spotpdf.cli/v1",
  "spotpdf_version": "0.7.1"
}
```

The common fields are:

| Field | Contract |
| --- | --- |
| `schema_version` | Exact wire schema identifier. Consumers should require `spotpdf.cli/v1`. |
| `spotpdf_version` | Installed application version for diagnostics. It is not the schema version. |
| `command` | Parsed command name, or `null` if parsing failed before a command was selected. |
| `ok` | `true` when the requested command or predicate completed, even if `check` exits with `2`. |
| `exit_code` | The same integer returned to the parent process. |
| `result` | Present only when `ok` is `true`. |
| `error` | Present only when `ok` is `false`. |

JSON object key order and whitespace are not semantic API guarantees. Current
output is compact and key-sorted only to make logs and regression comparisons
reproducible.

## Command results

Every mutating command requires exactly one of `-o/--output` or `--dry-run`.
Normal mutation results retain their existing v1 shape: they include `input`
and `output` and do not include `dry_run`. A successful dry run instead includes
`"dry_run":true` and omits `output`; all other command-specific result fields
are identical to a normal run. For example, the result fragment is:

```json
{"result":{"dry_run":true,"input":"input.pdf","selection":{"mode":"spot","spot":"Varnish"},"stats":{"changed":true,"fills_removed":1,"forms_changed":0,"pages_changed":[1],"resources_removed":1,"strokes_removed":0,"text_blocks":0,"text_show_operations":0}}}
```

This is a verified execution mode, not a read-only estimate. The same input
budgets, safety analysis, in-memory mutation, serialization, strict reopen, and
semantic verification run against a private temporary PDF. That PDF is deleted
before the success record is written. Handled mutation failures preserve the
normal exit/error contract and remove the dry-run PDF during context cleanup. A
filesystem cleanup failure is itself reported as an `io_error` before any
success record. `--force` has no effect in this mode.

### `list`

`result` contains `input`, `colorant_count`, and `colorants`. Each colorant has
these v1 fields; consumers must still ignore unknown additive fields:

- `name`: exact decoded PDF colorant name;
- `roles`: sorted values from `spot`, `process`, `all`, and `none`;
- `kinds`: sorted declaration kinds from `Separation`, `DeviceN`,
  `SeparationInfo`, and `Special`; tolerate unknown future kinds;
- `pages`: sorted one-based page numbers with supported paint;
- `paint_operations`: inventoried paint-operation count; and
- `contexts`: sorted diagnostic status and hazard descriptions. Their wording
  is not stable API; do not branch on it.

An empty inventory is always `"colorant_count":0,"colorants":[]`; it never
changes to the sentence used by the default text output.

### `check`

`result` contains `input`, the exact requested `spot`, and Boolean `present`.
A present name is a successfully evaluated predicate:

```json
{"command":"check","exit_code":2,"ok":true,"result":{"input":"input.pdf","present":true,"spot":"Varnish"},"schema_version":"spotpdf.cli/v1","spotpdf_version":"0.7.1"}
```

### `remove`

Exact selection contains:

```json
"selection":{"mode":"spot","spot":"Varnish"}
```

All-mode selection contains `"selection":{"mode":"all"}` plus the sorted
`spots_removed` array. Both modes include `input` and `stats`, plus the
publication field described above: `output` for a normal mutation or
`dry_run:true` for a dry run.

Removal `stats` always has:

- `changed`;
- `pages_changed`;
- `forms_changed`;
- `text_blocks`;
- `text_show_operations`;
- `fills_removed`;
- `strokes_removed`; and
- `resources_removed`.

In all-mode, if nothing is removable, `spots_removed` and `pages_changed` are
empty, `changed` is false, and every counter is zero. A normal run still copies
the input byte-for-byte to the requested output; a dry run verifies that same
private copy and then discards it. Exact-name mode does not include a
`spots_removed` field.

### `rename`

`result` contains `input`, `source`, `destination`, `definitions_renamed`, and
`references_renamed`, plus `output` or `dry_run:true` according to the shared
mutation rule above.

### `set-alternate`

`result` contains `input`, `spot`, four numeric `cmyk_percentages`, and
`definitions_changed`, plus `output` or `dry_run:true` according to the shared
mutation rule above. Percentages report the values actually stored after
PDF-number normalization.

### `convert`

`result` contains `input`, `spot`, four numeric `cmyk_percentages`,
`definitions_removed`, `resources_removed`,
`page_content_sequences_changed`, `forms_changed`,
`color_operators_rewritten`, and sorted `pages_affected`, plus `output` or
`dry_run:true` according to the shared mutation rule above.

## Error codes

Automation should branch on `error.code`, not on `error.message`. Messages are
human-readable context and may improve without a schema change. Treat an
unknown future code as a generic failure while preserving it for diagnostics.

| Code | Meaning | Structured details |
| --- | --- | --- |
| `usage_error` | Missing, conflicting, unknown, or invalid CLI arguments | Empty object |
| `budget_exceeded` | One deterministic processing budget was exceeded | `metric`, `field`, `observed`, `limit`, `option` |
| `unsupported_spot_use` | The requested mutation reached unsupported spot semantics | Empty object |
| `validation_error` | Input, requested operation, or publication validation failed | Empty object |
| `pdf_error` | A native PDF parser error reached the CLI boundary | Empty object |
| `io_error` | Operating-system I/O failed | Optional numeric `errno` |
| `invalid_input` | A runtime value failed type or value validation | Empty object |
| `processing_error` | Another fail-closed processing invariant failed | Empty object |
| `nesting_limit_exceeded` | PDF nesting exceeded a safe fixed limit | Empty object |

`validation_error` is intentionally broader than `invalid_pdf`. The current
Python exception also represents missing source names, reserved requests,
existing output conflicts, and other validation failures. Consumers should use
the stable code and treat the message only as diagnostic text.

## Exit codes

| Exit | JSON meaning |
| ---: | --- |
| `0` | Command completed, or `check` reported absent; `ok` is true. |
| `1` | Runtime, PDF, validation, I/O, or budget failure; `ok` is false. |
| `2` | `check` reported present; `ok` is true. |
| `64` | CLI usage or option-value error; `ok` is false. |

Help and version exit with `0` and remain text. In v0.6.0 and later, argument
errors use `64`, leaving `2` unambiguous for a successful present result.

## Enfocus Switch

This recipe requires spotpdf v0.6.0 or later.

For a removal step using **Execute command App v11**
([Switch 2022 Fall or newer](https://www0.enfocus.com/en/appstore/product/execute-command)):

```text
Command or path: /absolute/path/to/spotpdf
Arguments:       --format json remove "%1" --all -o "%2"
Output:          File at path
Output extension: Automatic
Exit code:       Set as private data (key: spotpdf-exit-code)
Stdout:          Attach as dataset (name: spotpdf-json)
Stderr:          Attach as dataset (name: spotpdf-error)
Fail if exit code is: Nonzero
```

Use the absolute executable path and quote `%1` and `%2`. Switch substitutes
`%1` with the input path and `%2` with a not-yet-existing output path when
**File at path** is selected. Do not add `--force`: the Switch-provided `%2`
must not exist yet. Mutation commands return `0` only after strict output
verification and atomic publication; runtime errors return `1` and usage errors
return `64`.

Switch invokes the executable directly rather than through a shell. Execute
command v11 can attach stdout and stderr as opaque datasets and preserve the
exit code as private job data; its debug log also records the substituted
command and all three channels. Shell redirection still requires a wrapper
script. These placeholders and stream options are documented in Enfocus's
[Execute command v11 guide](https://cdn-www0.enfocus.com/sites/default/files/media/images/appstore/product_documentation/1712221405/switch_apps_execute_command_v11.pdf).

Do not configure `check` with a generic “fail on nonzero” rule: a found name
intentionally returns `2`. Use `list` JSON for Switch-side routing, or add a
small wrapper that interprets `check` result `2` as a successful predicate.

## Shell and CI recipes

Create an inventory artifact and fail unless no spot-role colorants remain:

```bash
set -euo pipefail

spotpdf --format json list "$PDF" > spotpdf-report.json
jq -e '
  .schema_version == "spotpdf.cli/v1"
  and .ok == true
  and all(.result.colorants[]; (.roles | index("spot")) == null)
' spotpdf-report.json
```

Capture a mutating command without mixing success and error records:

```bash
if spotpdf --format json remove input.pdf --all -o output.pdf \
    > result.json 2> error.json; then
  jq -e '.ok == true and .command == "remove"' result.json
else
  jq . error.json >&2
  exit 1
fi
```

Preflight the same mutation without creating `output.pdf`:

```bash
if spotpdf --format json remove input.pdf --all --dry-run \
    > result.json 2> error.json; then
  jq -e '.ok == true and .result.dry_run == true and
         (.result | has("output") | not)' result.json
else
  jq . error.json >&2
  exit 1
fi
```

When processing a directory, give every input a distinct output path. Never
write back to the input, and do not run independent workers against the same
destination.

## Schema evolution and data safety

Consumers must require the exact `schema_version` they understand and ignore
unknown additive fields. Adding a field or a new error code is compatible
within v1. Removing or renaming a field, changing its type or meaning, or
changing stream placement requires a new schema identifier.

PDF colorant names, paths, contexts, and messages are untrusted data. Parse
JSON with a real JSON parser. Do not use `eval`, interpolate values into a
shell command, or treat an `error.message` as executable text. The JSON
contract belongs only to the CLI; the Python library continues to expose its
typed result objects and exceptions directly.
