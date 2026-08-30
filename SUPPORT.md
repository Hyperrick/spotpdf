# Support

`spotpdf` is maintained as a focused open-source project. Community support is
provided through GitHub on a best-effort basis; there is no guaranteed response
time or private file-analysis service.

## Choose the right channel

- For installation, command-line, compatibility, or workflow questions, open a
  [usage question](https://github.com/Hyperrick/spotpdf/issues/new?template=question.yml).
- For reproducible incorrect behavior, open a
  [bug report](https://github.com/Hyperrick/spotpdf/issues/new?template=bug_report.yml).
- For a new PDF construct or workflow, open a
  [feature request](https://github.com/Hyperrick/spotpdf/issues/new?template=feature_request.yml).
- For a vulnerability, use
  [GitHub private vulnerability reporting](https://github.com/Hyperrick/spotpdf/security/advisories/new).
  Do not disclose it in a public issue.

Check the [troubleshooting guide](docs/troubleshooting.md) and search the existing
issues first. A fail-closed refusal for a construct listed as unsupported in
[PDF compatibility](docs/compatibility.md) is expected behavior, not silent data
loss. A narrowly scoped request to support that construct is still welcome.

## What to include

Please provide:

- the exact output of `spotpdf --version`;
- the Python version, operating system, and architecture;
- the complete command, stdout, stderr, and exit code;
- the relevant `spotpdf list` output;
- whether the PDF is signed, encrypted, or modification-restricted; and
- a minimal synthetic reproducer or a canonical link to a clearly licensed public
  fixture when the question depends on a particular PDF structure.

Review pasted commands and output before posting. Replace sensitive file paths,
document names, spot names, job identifiers, and similar customer data with clear
placeholders while preserving the structure needed to understand the problem.

The [quick start](README.md#quick-start),
[troubleshooting guide](docs/troubleshooting.md),
[compatibility matrix](docs/compatibility.md), and
[JSON automation guide](docs/json-output.md) cover the supported operations and
stable automation contract.

## Protect document data

Do not upload customer, production, confidential, or personal PDFs through any
project channel, including private vulnerability reports. Removing visible text or
artwork is not reliable anonymization: PDFs can contain metadata, attachments,
unused objects, annotations, thumbnails, or other recoverable data.

Generate the smallest equivalent PDF with code whenever possible. If a reproducer
cannot be shared safely, describe the relevant object structure and observed output
without uploading the file. Private vulnerability reporting is for vulnerability
details and private reproduction steps, not confidential document analysis. The
project does not provide confidential document-processing or retention services.
