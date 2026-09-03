"""Run identifiers, checksums, and provenance manifests."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .config import PipelineConfig, SCHEMA_VERSION


def sha256_file(path: str | Path) -> str:
    """Return a streaming SHA-256 digest for a local source file."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_commit(repo_root: str | Path) -> str | None:
    """Return the producing Git commit when available."""

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def normalize_extract_timestamp(value: object) -> str | None:
    """Normalize the workbook's UTC extraction timestamp."""

    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            return text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def make_run_fingerprint(
    *,
    source_sha256: str,
    demographics_sha256: str,
    analysis_as_of_date: str,
    config: PipelineConfig,
) -> str:
    """Hash every input or rule that can materially change a run."""

    payload = {
        "source_sha256": source_sha256,
        "demographics_sha256": demographics_sha256,
        "analysis_as_of_date": analysis_as_of_date,
        "pipeline_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "participant_id_corrections": config.participant_id_corrections,
        "test_participant_ids": sorted(config.test_participant_ids),
        "missing_sentinels": sorted(config.missing_sentinels),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def make_run_id(extract_timestamp: str | None, run_fingerprint: str) -> str:
    """Create a deterministic run ID from extraction time and all run inputs."""

    timestamp = extract_timestamp
    try:
        if timestamp:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        else:
            raise ValueError
    except ValueError:
        dt = datetime.now(timezone.utc)
    return f"run_{dt.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}_{run_fingerprint[:8]}"


def build_manifest(
    *,
    run_id: str,
    status: str,
    workbook_path: Path,
    demographics_path: Path,
    source_sha256: str,
    demographics_sha256: str,
    run_fingerprint: str,
    source_extract_utc: str | None,
    analysis_as_of_date: str,
    table_row_counts: dict[str, int],
    qc_error_count: int,
    qc_warning_count: int,
    config: PipelineConfig,
    repo_root: Path,
) -> dict[str, Any]:
    """Build the machine-readable provenance record for one run."""

    return {
        "run_id": run_id,
        "status": status,
        "created_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_extract_utc": source_extract_utc,
        "analysis_as_of_date": analysis_as_of_date,
        "source_filename": workbook_path.name,
        "source_size_bytes": workbook_path.stat().st_size,
        "source_sha256": source_sha256,
        "demographics_filename": demographics_path.name,
        "demographics_size_bytes": demographics_path.stat().st_size,
        "demographics_sha256": demographics_sha256,
        "run_fingerprint": run_fingerprint,
        "source_box_file_id": None,
        "source_box_version_id": None,
        "pipeline_version": __version__,
        "pipeline_git_commit": git_commit(repo_root),
        "schema_version": SCHEMA_VERSION,
        "table_row_counts": table_row_counts,
        "qc_error_count": qc_error_count,
        "qc_warning_count": qc_warning_count,
        "participant_id_corrections": config.participant_id_corrections,
        "excluded_test_participant_ids": sorted(config.test_participant_ids),
        "missing_sentinels": sorted(config.missing_sentinels),
    }


def default_analysis_date(diary_dates: list[str], extract_timestamp: str | None) -> str:
    """Choose a deterministic inclusive cutoff without dropping source data."""

    valid_dates = []
    for value in diary_dates:
        try:
            valid_dates.append(date.fromisoformat(str(value)[:10]))
        except ValueError:
            continue
    if valid_dates:
        return max(valid_dates).isoformat()
    if extract_timestamp:
        try:
            return datetime.fromisoformat(extract_timestamp.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
    raise ValueError("Could not determine an analysis cutoff date")
