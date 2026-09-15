"""End-to-end Box orchestration using private temporary workspaces."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Sequence

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
from .storage import (
    BoxGateway,
    BoxPublication,
    BoxStudyStorage,
)
from .box_config import BoxSettings
from .reporting import ReportSetPlan, validate_report_set
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


@dataclass(frozen=True)
class BoxRunOutcome:
    run_id: str
    status: str
    qc_error_count: int
    qc_warning_count: int
    table_row_counts: dict[str, int]
    publication: BoxPublication


@dataclass(frozen=True)
class BoxReportOutcome:
    plan: ReportSetPlan
    publication: BoxPublication


def _write_csv(
    path: Path, rows: list[dict[str, Any]], fieldnames: Sequence[str]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _build_processed_run(
    *,
    workbook_path: str | Path,
    demographics_path: str | Path,
    workspace_root: str | Path,
    analysis_as_of_date: str | None = None,
    config: PipelineConfig | None = None,
    source_box: dict[str, Any] | None = None,
    demographics_box: dict[str, Any] | None = None,
) -> RunOutcome:
    """Materialize one processed run inside a caller-owned workspace."""

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
    destination = Path(workspace_root) / run_id
    destination.mkdir(parents=True, exist_ok=False)

    for table_name, rows in transformed.tables.items():
        _write_csv(destination / f"{table_name}.csv", rows, TABLE_FIELDS[table_name])
    _write_csv(
        destination / "qc_results.csv",
        [result.to_dict() for result in qc_results],
        QC_FIELDS,
    )
    (destination / "data_dictionary.md").write_text(
        DATA_DICTIONARY, encoding="utf-8"
    )

    output_files = {
        path.name: {
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(destination.iterdir())
        if path.is_file()
    }

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
        output_files=output_files,
        config=cfg,
        repo_root=repo_root,
        source_box=source_box,
        demographics_box=demographics_box,
    )
    (destination / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return RunOutcome(
        run_id=run_id,
        status="passed" if passed else "failed",
        output_directory=destination,
        qc_error_count=errors,
        qc_warning_count=warnings,
        table_row_counts=table_row_counts,
    )


def run_box_pipeline(
    *,
    workbook_file_id: str,
    demographics_file_id: str,
    settings: BoxSettings,
    analysis_as_of_date: str | None = None,
    config: PipelineConfig | None = None,
    gateway: BoxGateway | None = None,
) -> BoxRunOutcome:
    """Process explicit Box inputs and publish without retaining local data."""

    box_storage = BoxStudyStorage(settings, gateway=gateway)
    with tempfile.TemporaryDirectory(prefix="amma-box-inputs-") as temp_dir:
        temp_root = Path(temp_dir)
        os.chmod(temp_root, 0o700)
        workbook_path = temp_root / "amma_export.xlsx"
        demographics_path = temp_root / "demographics.csv"
        source = box_storage.download_input(
            workbook_file_id,
            settings.raw_folder_id,
            workbook_path,
            expected_suffix=".xlsx",
        )
        demographics = box_storage.download_input(
            demographics_file_id,
            settings.identifiers_folder_id,
            demographics_path,
            expected_suffix=".csv",
        )

        local = _build_processed_run(
            workbook_path=workbook_path,
            demographics_path=demographics_path,
            workspace_root=temp_root / "processed",
            analysis_as_of_date=analysis_as_of_date,
            config=config,
            source_box=source.manifest_dict(),
            demographics_box=demographics.manifest_dict(),
        )

        publication = box_storage.publish_run(
            local_run_directory=local.output_directory,
            run_id=local.run_id,
            passed=local.status == "passed",
            source=source,
            demographics=demographics,
        )
        return BoxRunOutcome(
            run_id=local.run_id,
            status=local.status,
            qc_error_count=local.qc_error_count,
            qc_warning_count=local.qc_warning_count,
            table_row_counts=local.table_row_counts,
            publication=publication,
        )


def _run_monthly_report_script(
    processed_run_directory: Path,
    reports_root: Path,
    demographics_path: Path,
    reporting_script: Path,
) -> None:
    try:
        completed = subprocess.run(
            [
                "Rscript",
                str(reporting_script),
                str(processed_run_directory),
                str(reports_root),
                str(demographics_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise RuntimeError("Could not start Rscript for patient report generation") from exc
    if completed.returncode != 0:
        raise RuntimeError(
            f"Patient report generation failed (exit code {completed.returncode})"
        )


def run_box_report_pipeline(
    *,
    source_run_id: str,
    demographics_file_id: str,
    settings: BoxSettings,
    reporting_script: str | Path,
    gateway: BoxGateway | None = None,
    report_runner: Callable[[Path, Path, Path, Path], None] | None = None,
) -> BoxReportOutcome:
    """Generate and publish reports from Box without retaining local data."""

    box_storage = BoxStudyStorage(settings, gateway=gateway)
    script_path = Path(reporting_script).resolve()
    if not script_path.is_file():
        raise FileNotFoundError("Reporting script does not exist")
    runner = report_runner or _run_monthly_report_script

    with tempfile.TemporaryDirectory(prefix="amma-box-reports-") as temp_dir:
        temp_root = Path(temp_dir)
        os.chmod(temp_root, 0o700)
        processed_run_directory = temp_root / "processed-run"
        reports_root = temp_root / "reports"
        demographics_path = temp_root / "demographics.csv"

        box_storage.download_processed_run(
            source_run_id, processed_run_directory
        )
        demographics = box_storage.download_input(
            demographics_file_id,
            settings.identifiers_folder_id,
            demographics_path,
            expected_suffix=".csv",
        )
        runner(
            processed_run_directory,
            reports_root,
            demographics_path,
            script_path,
        )
        report_directory = reports_root / source_run_id
        plan = validate_report_set(
            report_directory,
            source_run_id=source_run_id,
            reporting_script=script_path,
            demographics_box=demographics.manifest_dict(),
            demographics_sha256=sha256_file(demographics_path),
        )
        publication = box_storage.publish_report_set(plan)
        return BoxReportOutcome(plan=plan, publication=publication)
