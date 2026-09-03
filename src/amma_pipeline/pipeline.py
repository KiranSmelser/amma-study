"""End-to-end orchestration for one local Amma processing run."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from .config import (
    FORM_RESPONSE_FIELDS,
    PARTICIPANT_DAY_FIELDS,
    PARTICIPANT_FIELDS,
    QC_FIELDS,
    SEIZURE_EVENT_FIELDS,
    SEIZURE_TYPE_FIELDS,
    TRIGGER_FIELDS,
    PipelineConfig,
)
from .data_dictionary import DATA_DICTIONARY
from .manifest import (
    build_manifest,
    default_analysis_date,
    make_run_id,
    make_run_fingerprint,
    normalize_extract_timestamp,
    sha256_file,
)
from .storage import LocalRunStorage
from .transform import transform
from .validation import error_count, validate, warning_count
from .workbook_reader import read_demographics, read_workbook


TABLE_FIELDS: dict[str, Sequence[str]] = {
    "participants": PARTICIPANT_FIELDS,
    "participant_days": PARTICIPANT_DAY_FIELDS,
    "seizure_events": SEIZURE_EVENT_FIELDS,
    "seizure_event_triggers": TRIGGER_FIELDS,
    "seizure_type_dictionary": SEIZURE_TYPE_FIELDS,
    "form_responses": FORM_RESPONSE_FIELDS,
}


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    status: str
    output_directory: Path
    qc_error_count: int
    qc_warning_count: int
    table_row_counts: dict[str, int]


def _write_csv(
    path: Path, rows: list[dict[str, Any]], fieldnames: Sequence[str]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run_local_pipeline(
    *,
    workbook_path: str | Path,
    demographics_path: str | Path,
    output_root: str | Path,
    analysis_as_of_date: str | None = None,
    config: PipelineConfig | None = None,
) -> RunOutcome:
    """Process one local cumulative export and publish it atomically."""

    cfg = config or PipelineConfig()
    source_path = Path(workbook_path).resolve()
    demo_path = Path(demographics_path).resolve()
    source_sha256 = sha256_file(source_path)
    demo_sha256 = sha256_file(demo_path)

    workbook = read_workbook(source_path)
    demographics = read_demographics(demo_path)
    extract_utc = normalize_extract_timestamp(
        workbook.extract_metadata.get("Extract date (UTC)")
    )

    diary_dates = [str(row.get("Date") or "") for row in workbook.tables["Diary"]]
    cutoff = analysis_as_of_date or default_analysis_date(diary_dates, extract_utc)
    # Validate the explicit date early and normalize it.
    try:
        cutoff = date.fromisoformat(cutoff).isoformat()
    except ValueError as exc:
        raise ValueError("analysis_as_of_date must use YYYY-MM-DD") from exc

    transformed = transform(
        workbook,
        demographics,
        source_sha256=source_sha256,
        analysis_as_of_date=cutoff,
        config=cfg,
    )
    qc_results = validate(
        transformed,
        analysis_as_of_date=cutoff,
        config=cfg,
    )
    errors = error_count(qc_results)
    warnings = warning_count(qc_results)
    passed = errors == 0

    run_fingerprint = make_run_fingerprint(
        source_sha256=source_sha256,
        demographics_sha256=demo_sha256,
        analysis_as_of_date=cutoff,
        config=cfg,
    )
    run_id = make_run_id(extract_utc, run_fingerprint)
    storage = LocalRunStorage(output_root)
    staging = storage.create_staging_directory(run_id)

    for table_name, rows in transformed.tables.items():
        _write_csv(staging / f"{table_name}.csv", rows, TABLE_FIELDS[table_name])
    _write_csv(
        staging / "qc_results.csv",
        [result.to_dict() for result in qc_results],
        QC_FIELDS,
    )
    (staging / "data_dictionary.md").write_text(
        DATA_DICTIONARY, encoding="utf-8"
    )

    table_row_counts = {
        table_name: len(rows) for table_name, rows in transformed.tables.items()
    }
    repo_root = Path(__file__).resolve().parents[2]
    manifest = build_manifest(
        run_id=run_id,
        status="passed" if passed else "failed",
        workbook_path=source_path,
        demographics_path=demo_path,
        source_sha256=source_sha256,
        demographics_sha256=demo_sha256,
        run_fingerprint=run_fingerprint,
        source_extract_utc=extract_utc,
        analysis_as_of_date=cutoff,
        table_row_counts=table_row_counts,
        qc_error_count=errors,
        qc_warning_count=warnings,
        config=cfg,
        repo_root=repo_root,
    )
    (staging / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    destination = storage.publish(staging, run_id, passed=passed)
    return RunOutcome(
        run_id=run_id,
        status="passed" if passed else "failed",
        output_directory=destination,
        qc_error_count=errors,
        qc_warning_count=warnings,
        table_row_counts=table_row_counts,
    )
