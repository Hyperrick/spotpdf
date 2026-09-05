# Troubleshooting

`spotpdf` fails closed: a mutating command publishes no new output and leaves an
existing destination unchanged when it cannot prove that the requested change is
safe. Use the complete error message for context. With `--format json`, branch on
the stable `error.code`, not on the human-readable message.

## Output already exists

**Symptom:** The command reports `output already exists (use --force)` or JSON
`error.code: "validation_error"`.

**Cause:** Mutating commands protect an existing destination by default. The input
and output must also identify different files.

**Safe next action:** Choose a new `-o/--output` path, or add `--force` only when
you intentionally want to replace that exact destination:

```console
spotpdf remove input.pdf --all -o output.pdf --force
```

`--force` changes only the destination-collision policy. It does not bypass PDF
validation, unsupported-construct checks, signatures, permissions, or processing
budgets. The old destination remains unchanged unless the replacement has been
written, reopened, and verified successfully.

## A spot name is absent or does not match

**Symptom:** `check` reports the requested name as absent, or `rename`,
`set-alternate`, or `convert` reports that its source is absent.

**Cause:** PDF names are exact and case-sensitive. For example, `Varnish`,
`varnish`, and `Varnish ` are different names. `list` also shows NChannel process
components, while `check` reports only spot/removal candidates.

**Safe next action:** Inventory the PDF and copy the exact displayed spot name:

```console
spotpdf list input.pdf
spotpdf check input.pdf --spot "Exact Name"
```

Do not normalize spelling or case in automation. An explicit `remove --spot` for
an absent name copies the input byte-for-byte to a new output; the other mutating
commands reject an absent source.

## The PDF is signed, encrypted, or modification-restricted

**Symptom:** A mutation reports `signed PDFs are not modified`, `encrypted PDFs
are not supported`, `the PDF permissions do not allow content modification`, or
that the PDF cannot be opened safely.

**Cause:** Rewriting invalidates signatures, and `spotpdf` does not bypass
encryption or modification permissions. Signed PDFs can be inventoried, while
inspection of encrypted or restricted inputs remains limited by the parser and
their permissions.

**Safe next action:** Preserve the original. Ask the document owner or producing
workflow for an authorized unsigned, unencrypted, modification-permitted working
copy, then process that copy. Do not remove a signature or access restriction merely
to make `spotpdf` accept the file.

## `unsupported_spot_use`

**Symptom:** JSON reports `error.code: "unsupported_spot_use"`, or text output
names an unsupported target use such as DeviceN/NChannel, an image, pattern,
shading, annotation appearance, Type 3 font, transparency, or context-dependent
Form.

**Cause:** Inventory is broader than mutation support. `list` can classify
DeviceN/NChannel colorants and process roles, but `remove`, `set-alternate`, and
`convert` reject target-related DeviceN/NChannel semantics. `rename` supports only
a structurally consistent spot component alongside a true `/Separation`. Other
constructs are rejected when changing them could alter unrelated artwork or leave
stale spot references.

**Safe next action:** Read the location in the error message, compare it with the
[compatibility matrix](compatibility.md), and keep the original PDF. `--force` and
higher processing budgets do not make unsupported semantics safe. Use the
originating design or prepress workflow to create a supported standalone
`/Separation` in vector/text content, or open a narrowly scoped feature request
with a minimal synthetic reproducer. Do not patch PDF objects or delete resource
entries as a workaround.

## Locate the failed object visually

Repeat the intended command with `--dry-run --report report.html` instead of a PDF
output path. The offline HTML links errors to original objects, source operators,
page previews, and enlarged excerpts where that correspondence is reliable.
`--report-overwrite` explicitly replaces an older report. A partial report explains
unmapped objects, diagnostic limits, and areas where analysis stopped; inspect
those notices before treating it as a complete inventory. See
[diagnostic reports](diagnostic-reports.md) for examples and failure semantics.

## A processing budget is exceeded

**Symptom:** Text output starts with `processing budget exceeded:`, or JSON reports
`error.code: "budget_exceeded"` with the metric, observed value, limit, and matching
CLI option.

**Cause:** The input exceeded one deterministic application ceiling for file bytes,
pages, reachable graph entries, decoded page/Form content, or content operators.
This is independent of PDF-construct compatibility.

**Safe next action:** Confirm that the PDF is trusted and that its size or structure
is expected. Then raise only the option named by the error, for example:

```console
spotpdf remove trusted-large.pdf --all -o clean.pdf --max-pages 20000
```

Do not raise a limit merely to suppress an unexpected result. These options are
application counters, not CPU/RAM isolation; use operating-system or container
limits for untrusted workloads. See the [processing-budget guide](processing-budgets.md)
for exact counting semantics.

## `pdftoppm`, qpdf, or Ghostscript is missing

**Symptom:** A documentation-image, render-comparison, or public-corpus command
reports that `pdftoppm`, `qpdf`, or Ghostscript (`gs`) is not installed or not on
`PATH`.

**Cause:** These executables are optional development and release tools. The
installed `spotpdf` commands (`list`, `check`, `remove`, `rename`, `set-alternate`,
and `convert`) do not invoke them. They are therefore not installed as CLI runtime
dependencies.

**Safe next action:** If you only use the installed CLI, no render tool is needed;
diagnose the actual `spotpdf` stderr instead. If you are contributing or running
release checks, install the tools required by that specific gate and verify them as
described in the [contributor setup](../CONTRIBUTING.md#development-setup). The
[public-corpus guide](public-corpus.md#run-locally) documents the full three-tool
release setup.

## Still blocked

Review the [support guide](../SUPPORT.md) before opening an issue. Share commands,
output, and a synthetic reproducer, but never upload a confidential customer or
production PDF.
