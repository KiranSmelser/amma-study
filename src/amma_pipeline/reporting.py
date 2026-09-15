"""Validation and deterministic identifiers for patient report sets."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import __version__
from .manifest import sha256_file


REPORT_SCHEMA_VERSION = "1.0.0"
REPORT_INDEX_FIELDS = (
    "participant_id",
    "month",
    "source_run",
    "relative_file",
)
SAFE_PARTICIPANT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class ReportValidationError(RuntimeError):
    """A safely printable patient-report validation failure."""


@dataclass(frozen=True)
class ReportSetPlan:
    report_set_id: str
    source_run_id: str
    report_directory: Path
    reporting_script_sha256: str
    demographics_sha256: str
    demographics_box: dict[str, object]
    publisher_version: str
    entries: tuple[dict[str, str], ...]


def validate_report_set(
    report_directory: str | Path,
    *,
    source_run_id: str,
    reporting_script: str | Path,
    demographics_box: dict[str, object],
    demographics_sha256: str,
) -> ReportSetPlan:
    """Validate report paths, metadata, PDFs, and source linkage."""

    if not re.fullmatch(r"[0-9a-f]{64}", demographics_sha256):
        raise ReportValidationError("Demographics SHA-256 is invalid")
    required_demographics_fields = {"file_id", "version_id", "box_sha1"}
    if not required_demographics_fields.issubset(demographics_box) or not all(
        demographics_box.get(field) for field in required_demographics_fields
    ):
        raise ReportValidationError("Demographics Box provenance is incomplete")

    root = Path(report_directory).resolve()
    index_path = root / "report_index.csv"
    manifest_path = root / "report_manifest.json"
    script_path = Path(reporting_script).resolve()
    if not root.is_dir():
        raise ReportValidationError("Patient report directory does not exist")
    if not index_path.is_file() or not manifest_path.is_file():
        raise ReportValidationError(
            "Patient report directory requires report_index.csv and report_manifest.json"
        )
    if not script_path.is_file():
        raise ReportValidationError("Reporting script does not exist")

    with index_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != REPORT_INDEX_FIELDS:
            raise ReportValidationError(
                "Report index must contain only participant_id, month, "
                "source_run, and relative_file"
            )
        entries = tuple(dict(row) for row in reader)
    if not entries:
        raise ReportValidationError("Report index contains no reports")

    indexed_paths: set[Path] = set()
    indexed_keys: set[tuple[str, str]] = set()
    for row in entries:
        participant_id = row["participant_id"].strip()
        month = row["month"].strip()
        if not SAFE_PARTICIPANT_ID.fullmatch(participant_id):
            raise ReportValidationError("Report index contains an unsafe participant ID")
        if not MONTH.fullmatch(month):
            raise ReportValidationError("Report index contains an invalid calendar month")
        if row["source_run"].strip() != source_run_id:
            raise ReportValidationError("Report index references the wrong source run")
        relative = PurePosixPath(row["relative_file"].strip())
        expected = PurePosixPath(
            participant_id, f"{participant_id}_{month}.pdf"
        )
        if relative != expected or relative.is_absolute() or ".." in relative.parts:
            raise ReportValidationError(
                "Report paths must use participant IDs and the approved naming convention"
            )
        key = (participant_id, month)
        if key in indexed_keys:
            raise ReportValidationError("Report index contains a duplicate patient-month")
        indexed_keys.add(key)
        local_path = root.joinpath(*relative.parts)
        if local_path.is_symlink() or not local_path.is_file():
            raise ReportValidationError("An indexed patient report file is missing")
        with local_path.open("rb") as handle:
            if handle.read(4) != b"%PDF":
                raise ReportValidationError("An indexed patient report is not a PDF")
        indexed_paths.add(local_path.resolve())

    actual_pdfs = {path.resolve() for path in root.rglob("*.pdf") if path.is_file()}
    if actual_pdfs != indexed_paths:
        raise ReportValidationError(
            "PDF files in the report directory do not exactly match the report index"
        )
    allowed_files = indexed_paths | {index_path.resolve(), manifest_path.resolve()}
    unexpected = {
        path.resolve()
        for path in root.rglob("*")
        if path.is_file() and path.resolve() not in allowed_files
    }
    if unexpected:
        raise ReportValidationError("Report directory contains unexpected files")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ReportValidationError("Report manifest is not valid JSON") from exc
    if manifest.get("status") != "passed":
        raise ReportValidationError("Report manifest did not pass validation")
    if manifest.get("source_run_id") != source_run_id:
        raise ReportValidationError("Report manifest references the wrong source run")
    if manifest.get("report_schema_version") != REPORT_SCHEMA_VERSION:
        raise ReportValidationError("Unsupported report manifest schema version")
    if manifest.get("report_count") != len(entries):
        raise ReportValidationError("Report manifest count does not match the index")

    script_sha256 = sha256_file(script_path)
    fingerprint_payload = {
        "source_run_id": source_run_id,
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "reporting_script_sha256": script_sha256,
        "demographics_sha256": demographics_sha256,
        "demographics_box_file_id": demographics_box.get("file_id"),
        "demographics_box_version_id": demographics_box.get("version_id"),
        "demographics_box_sha1": demographics_box.get("box_sha1"),
        "publisher_version": __version__,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    report_set_id = f"report_{source_run_id}_{fingerprint[:8]}"
    return ReportSetPlan(
        report_set_id=report_set_id,
        source_run_id=source_run_id,
        report_directory=root,
        reporting_script_sha256=script_sha256,
        demographics_sha256=demographics_sha256,
        demographics_box=dict(demographics_box),
        publisher_version=__version__,
        entries=entries,
    )
