# Security policy

## Supported versions

Only the latest published release receives security fixes.

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/Hyperrick/spotpdf/security/advisories/new).
Do not open a public issue for a security vulnerability and do not attach a
confidential PDF.

Please include:

- affected `spotpdf`, Python, and operating-system versions;
- a description of the impact and expected safe behavior;
- a minimal synthetic reproducer or private reproduction steps;
- whether the issue can overwrite output, escape the selected paths, exhaust
  resources, or publish a partially rewritten PDF.

You should receive an acknowledgement within seven days. Please allow time for
a fix and coordinated disclosure before publishing details.

## Processing untrusted PDFs

PDFs are complex, attacker-controlled object graphs. Protection has three
separate layers; none replaces the others.

### 1. qpdf parser and filter safeguards

pikepdf uses qpdf, whose global safeguards bound parser nesting, damaged-object
errors, container sizes, and stream-filter chains. These are native parser
controls. They do not provide spotpdf's file/page/graph/content/operator
budgets, and they are not general CPU, RAM, or wall-clock quotas. Keep pikepdf
and qpdf current. See qpdf's
[documented global limits](https://qpdf.readthedocs.io/en/latest/cli.html#global-limits).

### 2. spotpdf application budgets

Every input-processing CLI subcommand and each of the seven documented
path-based library operations has configurable ceilings for input bytes, pages,
reachable graph entries, decoded page/Form content bytes, and content
operators. The checks run in a deterministic order before mutation. An overrun
never publishes or replaces an output, and no private temporary candidate
remains. Defaults, API names, and exact counter semantics are documented in
[processing budgets](docs/processing-budgets.md).

The JSON mode escapes PDF-controlled names, paths, contexts,
and messages as data, but it does not make those values trustworthy. Parse the
document with a JSON parser and never pass returned strings to `eval` or an
unquoted shell command. See the [JSON automation contract](docs/json-output.md).

These are refusal points, not proof that a sub-limit PDF is safe. Native qpdf
must do work before Python can inspect the graph, and supported non-lossy content
filters are decoded before spotpdf can measure a stream's decoded length. The
decoded-content budget excludes images, ICC profiles, attachments, metadata, and other
non-content streams. qpdf's full syntax check may decode those streams later.
The input-byte limit also does not cap temporary output size.

### 3. external process isolation

For attacker-controlled or multi-tenant inputs, run `spotpdf` as an unprivileged
user in a disposable container or equivalent operating-system sandbox. Set
independent CPU, address-space/RAM, wall-clock, output/temp-disk, process,
file-descriptor, and filesystem-permission limits. Do not rely on Python
`tracemalloc`: it does not account for all native qpdf allocations. Never give a
bulk worker more file or network access than the task requires.
