"""Optional CLI report lifecycle, bounded worker and atomic HTML publication."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from .cli_limits import processing_limits_from_args
from .cli_output import _classify_error, emit_runtime_error
from .diagnostics import Finding


def positive(value):
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def add_report_arguments(parser):
    parser.add_argument("--report", type=Path, help="write an offline HTML diagnostic report")
    parser.add_argument(
        "--report-overwrite",
        action="store_true",
        help="replace an existing report (never the input or PDF output)",
    )
    for option, default in [
        ("max-findings", 1000),
        ("max-pages", 100),
        ("max-bytes", 50 * 1024 * 1024),
        ("timeout", 120),
    ]:
        parser.add_argument(
            "--report-" + option,
            type=positive,
            default=default,
            help=f"report {option.replace('-', ' ')} (default: {default})",
        )


def validate_destination(args):
    path = args.report
    for protected in [args.input, args.output]:
        if protected is None:
            continue
        if path.resolve() == protected.resolve() or (
            path.exists() and protected.exists() and os.path.samefile(path, protected)
        ):
            raise ValueError("Report path aliases the input or PDF output")
    if path.is_symlink():
        raise ValueError("Report destination must not be a symbolic link")
    if path.exists() and (not args.report_overwrite or not path.is_file()):
        raise ValueError("Report already exists; use --report-overwrite for a regular file")
    if not path.parent.is_dir():
        raise ValueError("Report parent directory does not exist")


def publish(args, content):
    validate_destination(args)
    descriptor, temporary = tempfile.mkstemp(prefix=".spotpdf-report-", dir=args.report.parent)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        if args.report_overwrite:
            os.replace(temporary, args.report)
        else:
            os.link(temporary, args.report)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_report(args, execute):
    try:
        validate_destination(args)
        limits = processing_limits_from_args(args)
    except (ValueError, OSError) as error:
        emit_runtime_error(args.command, error, args.format)
        return 1
    try:
        source_stat = args.input.stat()
        source_signature = [
            source_stat.st_dev,
            source_stat.st_ino,
            source_stat.st_size,
            source_stat.st_mtime_ns,
        ]
    except OSError:
        source_signature = None
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        status = execute(args)
    error = getattr(args, "_operation_error", None)
    findings = []
    if error is not None:
        code, message, _ = _classify_error(error)
        findings = getattr(error, "findings", []) or [
            Finding(code, message, [args.spot] if getattr(args, "spot", None) else [])
        ]
        for finding in findings:
            finding.primary = True
    request = {
        key: getattr(args, key, None)
        for key in ("command", "spot", "all_spots", "destination", "cmyk", "to_cmyk", "dry_run")
    }
    request.update(
        input=str(args.input.resolve()),
        input_name=args.input.name,
        failed=bool(status),
        findings=[f.wire() for f in findings],
        limits=asdict(limits),
        skip_input=bool(getattr(error, "input_validation_failed", False)),
        gaps=[],
        source_signature=source_signature,
    )
    for field in ("max_findings", "max_pages", "max_bytes", "timeout"):
        request[field] = getattr(args, "report_" + field)
    if request["skip_input"]:
        request["gaps"].append("Input failed strict validation; no diagnostic reopen or rendering")
    report = {
        "path": str(args.report),
        "status": "failed",
        "gaps": [],
        "output_published": status == 0 and not args.dry_run,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="spotpdf-report-") as name:
            directory = Path(name)
            (directory / "request.json").write_text(json.dumps(request), encoding="utf-8")
            # Initial fallback is generated without opening the input, in the parent process.
            from .report_worker import write_result

            write_result(request, directory, findings, request["gaps"], [])
            try:
                completed = subprocess.run(
                    [sys.executable, "-m", "spotpdf.report_worker", name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=args.report_timeout,
                    check=False,
                )
                worker_error = "Diagnostic worker failed" if completed.returncode else None
            except subprocess.TimeoutExpired:
                worker_error = "Diagnostic timeout reached; unfinished areas omitted"
            metadata = json.loads((directory / "metadata.json").read_text())
            if worker_error:
                # Rebuild from checkpoint so incomplete coverage is visible in HTML too.
                metadata["gaps"].append(worker_error)
                write_result(
                    request,
                    directory,
                    [Finding(**f) for f in metadata["findings"]],
                    metadata["gaps"],
                    [],
                )
                metadata = json.loads((directory / "metadata.json").read_text())
            publish(args, (directory / "report.html").read_bytes())
            report.update({k: metadata[k] for k in ("status", "gaps")})
            findings = [Finding(**f) for f in metadata["findings"]]
    except Exception as report_error:
        report["gaps"].append(str(report_error))
    final_status = status or (1 if report["status"] == "failed" else 0)
    if args.format == "json":
        payload = json.loads(stderr.getvalue() if status else stdout.getvalue())
        payload["report"] = report
        if status and findings:
            payload["error"]["details"]["findings"] = [f.wire() for f in findings]
        if not status and final_status:
            payload.update(
                ok=False,
                exit_code=1,
                error={
                    "code": "report_error",
                    "message": "Report could not be written",
                    "details": {"output_published": report["output_published"]},
                },
            )
        print(
            json.dumps(payload, ensure_ascii=True), file=sys.stderr if final_status else sys.stdout
        )
    else:
        sys.stdout.write(stdout.getvalue())
        sys.stderr.write(stderr.getvalue())
        print(f"spotpdf: report {report['status']}: {args.report}", file=sys.stderr)
        for gap in report["gaps"]:
            print(f"spotpdf: report: {gap}", file=sys.stderr)
        if report["status"] == "failed" and report["output_published"]:
            print("spotpdf: output PDF was already published successfully", file=sys.stderr)
    return final_status
