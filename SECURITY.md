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

PDFs are complex, attacker-controlled object graphs. `spotpdf` uses pikepdf and
qpdf for parsing, rejects recovery warnings for mutations, bounds nested Form
processing, writes to a temporary file, and reopens the result before atomic
publication. These controls reduce risk but are not a sandbox.

For untrusted inputs, run the CLI as an unprivileged user inside a container or
other operating-system sandbox with explicit CPU, memory, file-size, and time
limits. Keep pikepdf and qpdf current. Never run bulk processing with more file
permissions than the task requires.
