"""Download and exercise the pinned public spot-color PDF corpus."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spotpdf.alternate import validate_cmyk_percentages
from spotpdf.document import inspect_pdf
from spotpdf.model import InspectionReport, InvalidPdfError

_DOWNLOAD_TIMEOUT_SECONDS = 60
_COMMAND_TIMEOUT_SECONDS = 180
_PLATE_NAME = re.compile(r"\((.+)\)\.tif$")


@dataclass(frozen=True)
class CorpusCase:
    """One immutable public PDF and its expected operation semantics."""

    id: str
    filename: str
    source: str
    source_commit: str
    url: str
    sha256: str
    size: int
    license: str
    license_url: str
    operation: str
    source_spot: str | None = None
    destination_spot: str | None = None
    cmyk_percentages: tuple[float, float, float, float] | None = None
    remove_spots: tuple[str, ...] = ()
    preserve_names: tuple[str, ...] = ()
    same_composite: bool = False
    different_composite: bool = False
    byte_identical: bool = False
    expect_empty_inventory: bool = False


def load_manifest(path: Path) -> tuple[CorpusCase, ...]:
    """Load and validate the checked-in public-corpus manifest."""

    with path.open("rb") as manifest_file:
        manifest = tomllib.load(manifest_file)
    if manifest.get("version") != 1:
        raise ValueError("unsupported public corpus manifest version")
    raw_cases = manifest.get("case")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("public corpus manifest contains no cases")
    cases = tuple(_parse_case(raw) for raw in raw_cases)
    identifiers = [case.id for case in cases]
    filenames = [case.filename for case in cases]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("public corpus case ids must be unique")
    if len(set(filenames)) != len(filenames):
        raise ValueError("public corpus filenames must be unique")
    return cases


def run_public_corpus(
    manifest_path: Path,
    cache: Path,
    *,
    offline: bool,
) -> None:
    """Run every structural, composite, and separation-plate gate."""

    cases = load_manifest(manifest_path)
    tools = _required_tools()
    cache.mkdir(parents=True, exist_ok=True)
    _print_tool_versions(tools)
    for case in cases:
        print(f"\n[{case.id}] {case.source}", flush=True)
        source = obtain_case(case, cache, offline=offline)
        with tempfile.TemporaryDirectory(prefix=f"spotpdf-{case.id}-") as work_dir:
            _run_case(case, source, Path(work_dir), tools)
        print(f"[{case.id}] PASS", flush=True)


def obtain_case(case: CorpusCase, cache: Path, *, offline: bool) -> Path:
    """Return one hash-verified cached PDF, downloading it when allowed."""

    destination = cache / case.filename
    if destination.is_file() and _matches_case(destination, case):
        print(f"Using verified cache: {destination}", flush=True)
        return destination
    if offline:
        raise RuntimeError(f"verified offline corpus file is unavailable: {destination}")
    if destination.exists():
        raise RuntimeError(f"refusing to replace unverified corpus path: {destination}")

    request = urllib.request.Request(
        case.url,
        headers={"User-Agent": "spotpdf-public-corpus-gate/1"},
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{case.filename}.",
        suffix=".download",
        dir=cache,
    )
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    written = 0
    try:
        with (
            os.fdopen(descriptor, "wb") as output,
            urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response,
        ):
            if not response.geturl().startswith("https://"):
                raise RuntimeError(f"download redirected away from HTTPS for {case.id}")
            while chunk := response.read(64 * 1024):
                written += len(chunk)
                if written > case.size:
                    raise RuntimeError(f"download exceeds pinned size for {case.id}")
                digest.update(chunk)
                output.write(chunk)
        if written != case.size or digest.hexdigest() != case.sha256:
            raise RuntimeError(f"download hash or size mismatch for {case.id}")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def _parse_case(raw: Any) -> CorpusCase:
    if not isinstance(raw, dict):
        raise ValueError("every public corpus case must be a table")
    required_strings = (
        "id",
        "filename",
        "source",
        "source_commit",
        "url",
        "sha256",
        "license",
        "license_url",
        "operation",
    )
    values = {name: _required_string(raw, name) for name in required_strings}
    size = raw.get("bytes")
    if not isinstance(size, int) or size <= 0:
        raise ValueError(f"invalid byte size for corpus case {values['id']}")
    if not re.fullmatch(r"[0-9a-f]{40}", values["source_commit"]):
        raise ValueError(f"invalid source commit for corpus case {values['id']}")
    if not re.fullmatch(r"[0-9a-f]{64}", values["sha256"]):
        raise ValueError(f"invalid SHA-256 for corpus case {values['id']}")
    if not values["url"].startswith("https://") or not values["license_url"].startswith("https://"):
        raise ValueError(f"corpus URLs must use HTTPS for case {values['id']}")
    if Path(values["filename"]).name != values["filename"]:
        raise ValueError(f"corpus filename must not contain a path: {values['filename']}")

    operation = values["operation"]
    source_spot = _optional_string(raw, "source_spot")
    destination_spot = _optional_string(raw, "destination_spot")
    if operation == "rename" and (source_spot is None or destination_spot is None):
        raise ValueError(f"rename corpus case {values['id']} requires both spot names")
    cmyk_percentages = _optional_cmyk(raw, values["id"])
    if operation in {"set-alternate", "convert"} and (
        source_spot is None or cmyk_percentages is None
    ):
        raise ValueError(f"{operation} corpus case {values['id']} requires a spot and CMYK tuple")
    if operation not in {"rename", "remove-all", "set-alternate", "convert"}:
        raise ValueError(f"unsupported corpus operation: {operation}")
    same_composite = _optional_bool(raw, "same_composite")
    different_composite = _optional_bool(raw, "different_composite")
    if same_composite and different_composite:
        raise ValueError(f"corpus case {values['id']} has conflicting composite assertions")
    return CorpusCase(
        id=values["id"],
        filename=values["filename"],
        source=values["source"],
        source_commit=values["source_commit"],
        url=values["url"],
        sha256=values["sha256"],
        size=size,
        license=values["license"],
        license_url=values["license_url"],
        operation=operation,
        source_spot=source_spot,
        destination_spot=destination_spot,
        cmyk_percentages=cmyk_percentages,
        remove_spots=_string_tuple(raw, "remove_spots"),
        preserve_names=_string_tuple(raw, "preserve_names"),
        same_composite=same_composite,
        different_composite=different_composite,
        byte_identical=_optional_bool(raw, "byte_identical"),
        expect_empty_inventory=_optional_bool(raw, "expect_empty_inventory"),
    )


def _run_case(case: CorpusCase, source: Path, work: Path, tools: dict[str, str]) -> None:
    output = work / "output.pdf"
    _run([tools["qpdf"], "--check", str(source)])
    before = inspect_pdf(source)
    before_plates = _render_separations(tools["gs"], source, work / "plates-before")

    if case.operation == "rename":
        if case.source_spot not in before.colorants:
            raise RuntimeError(f"source spot is absent before rename: {case.source_spot}")
        _run_spotpdf(
            "rename",
            str(source),
            "--spot",
            case.source_spot or "",
            "--to",
            case.destination_spot or "",
            "-o",
            str(output),
        )
    elif case.operation == "set-alternate":
        percentages = case.cmyk_percentages or ()
        _run_spotpdf(
            "set-alternate",
            str(source),
            "--spot",
            case.source_spot or "",
            "--cmyk",
            ",".join(f"{value:g}" for value in percentages),
            "-o",
            str(output),
        )
    elif case.operation == "convert":
        percentages = case.cmyk_percentages or ()
        _run_spotpdf(
            "convert",
            str(source),
            "--spot",
            case.source_spot or "",
            "--to-cmyk",
            ",".join(f"{value:g}" for value in percentages),
            "-o",
            str(output),
        )
    else:
        _run_spotpdf("remove", str(source), "--all", "-o", str(output))

    _run([tools["qpdf"], "--check", str(output)])
    after = inspect_pdf(output)
    _verify_inventory(case, before, after)
    if case.byte_identical and source.read_bytes() != output.read_bytes():
        raise RuntimeError(f"{case.id} expected a byte-identical no-op copy")
    if case.same_composite:
        _verify_composite(tools["pdftoppm"], source, output, work, expect_equal=True)
    if case.different_composite:
        _verify_composite(tools["pdftoppm"], source, output, work, expect_equal=False)
    after_plates = _render_separations(tools["gs"], output, work / "plates-after")
    _verify_plates(case, before_plates, after_plates)


def _verify_inventory(
    case: CorpusCase,
    before: InspectionReport,
    after: InspectionReport,
) -> None:
    if case.operation == "rename":
        if case.source_spot in after.colorants:
            raise RuntimeError(f"stale source spot after rename: {case.source_spot}")
        if case.destination_spot not in after.colorants:
            raise RuntimeError(f"destination spot absent after rename: {case.destination_spot}")
    elif case.operation == "set-alternate":
        if case.source_spot not in before.colorants or case.source_spot not in after.colorants:
            raise RuntimeError("set-alternate changed or lost the source spot inventory")
        if set(before.colorants) != set(after.colorants):
            raise RuntimeError("set-alternate changed the named-colorant inventory")
    elif case.operation == "convert":
        if case.source_spot not in before.colorants:
            raise RuntimeError(f"source spot is absent before conversion: {case.source_spot}")
        if case.source_spot in after.colorants:
            raise RuntimeError(f"converted spot remains in inventory: {case.source_spot}")
    for name in case.remove_spots:
        if name in after.colorants:
            raise RuntimeError(f"removed spot remains in inventory: {name}")
    for name in case.preserve_names:
        if name not in before.colorants or name not in after.colorants:
            raise RuntimeError(f"required process component was not preserved: {name}")
    if case.expect_empty_inventory and after.colorants:
        names = sorted(after.colorants)
        raise RuntimeError(f"expected empty named-colorant inventory, found {names}")


def _verify_plates(case: CorpusCase, before: set[str], after: set[str]) -> None:
    if case.operation == "rename":
        if case.source_spot not in before:
            raise RuntimeError(f"Ghostscript did not render source plate: {case.source_spot}")
        if case.source_spot in after or case.destination_spot not in after:
            raise RuntimeError("Ghostscript plate names did not follow the rename")
    elif case.operation == "set-alternate":
        if case.source_spot not in before or before != after:
            raise RuntimeError("set-alternate changed the Ghostscript separation plate set")
    elif case.operation == "convert":
        if case.source_spot not in before or case.source_spot in after:
            raise RuntimeError("Ghostscript still reports the converted Separation plate")
    for name in case.remove_spots:
        if name not in before or name in after:
            raise RuntimeError(f"Ghostscript plate removal failed for {name}")
    for name in case.preserve_names:
        if name not in before or name not in after:
            raise RuntimeError(f"Ghostscript process plate was not preserved: {name}")


def _verify_composite(
    renderer: str,
    source: Path,
    output: Path,
    work: Path,
    *,
    expect_equal: bool,
) -> None:
    before = _render_composite(renderer, source, work / "composite-before")
    after = _render_composite(renderer, output, work / "composite-after")
    equal = len(before) == len(after) and all(
        left.read_bytes() == right.read_bytes() for left, right in zip(before, after, strict=True)
    )
    if equal != expect_equal:
        expectation = "stay unchanged" if expect_equal else "change"
        raise RuntimeError(f"Poppler composite render did not {expectation}")


def _render_composite(renderer: str, pdf: Path, directory: Path) -> list[Path]:
    directory.mkdir()
    prefix = directory / "page"
    _run([renderer, "-png", "-r", "144", str(pdf), str(prefix)])
    pages = sorted(directory.glob("page-*.png"))
    if not pages:
        raise RuntimeError("Poppler produced no composite pages")
    return pages


def _render_separations(renderer: str, pdf: Path, directory: Path) -> set[str]:
    directory.mkdir()
    _run(
        [
            renderer,
            "-q",
            "-dSAFER",
            "-dBATCH",
            "-dNOPAUSE",
            "-r144",
            "-sDEVICE=tiffsep",
            f"-sOutputFile={directory / 'plate-%d.tif'}",
            str(pdf),
        ]
    )
    plates = {
        match.group(1)
        for path in directory.glob("*.tif")
        if (match := _PLATE_NAME.search(path.name)) is not None
    }
    if not any(directory.glob("*.tif")):
        raise RuntimeError("Ghostscript produced no separation output")
    return plates


def _required_tools() -> dict[str, str]:
    tools: dict[str, str] = {}
    for name in ("qpdf", "pdftoppm", "gs"):
        executable = shutil.which(name)
        if executable is None:
            raise RuntimeError(f"public corpus gate requires {name} on PATH")
        tools[name] = executable
    return tools


def _print_tool_versions(tools: dict[str, str]) -> None:
    commands = {
        "qpdf": [tools["qpdf"], "--version"],
        "pdftoppm": [tools["pdftoppm"], "-v"],
        "gs": [tools["gs"], "--version"],
    }
    for name, command in commands.items():
        result = _run(command)
        version = (result.stdout or result.stderr).strip().splitlines()[0]
        print(f"{name}: {version}", flush=True)


def _run_spotpdf(*arguments: str) -> None:
    result = _run([sys.executable, "-m", "spotpdf", *arguments])
    if result.stdout.strip():
        print(result.stdout.strip(), flush=True)


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as error:
        details = (error.stderr or error.stdout or "no command output").strip()
        raise RuntimeError(f"command failed ({command[0]}): {details}") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"command timed out ({command[0]})") from error


def _matches_case(path: Path, case: CorpusCase) -> bool:
    if path.stat().st_size != case.size:
        return False
    return hashlib.sha256(path.read_bytes()).hexdigest() == case.sha256


def _required_string(raw: dict[str, Any], name: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing non-empty string field: {name}")
    return value


def _optional_string(raw: dict[str, Any], name: str) -> str | None:
    value = raw.get(name)
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"invalid optional string field: {name}")
    return value


def _string_tuple(raw: dict[str, Any], name: str) -> tuple[str, ...]:
    value = raw.get(name, [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"invalid string list field: {name}")
    return tuple(value)


def _optional_bool(raw: dict[str, Any], name: str) -> bool:
    value = raw.get(name, False)
    if not isinstance(value, bool):
        raise ValueError(f"invalid boolean field: {name}")
    return value


def _optional_cmyk(
    raw: dict[str, Any],
    case_id: str,
) -> tuple[float, float, float, float] | None:
    value = raw.get("cmyk_percentages")
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"invalid CMYK tuple for corpus case {case_id}")
    try:
        return validate_cmyk_percentages(value)
    except InvalidPdfError as error:
        raise ValueError(f"invalid CMYK tuple for corpus case {case_id}: {error}") from error


__all__ = ["CorpusCase", "load_manifest", "obtain_case", "run_public_corpus"]
