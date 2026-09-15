from __future__ import annotations

import csv
import json
from pathlib import Path

from amma_pipeline.pipeline import _build_processed_run


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_processed_run_builder_preserves_seizure_multiplicity(
    tmp_path: Path, synthetic_inputs
) -> None:
    workbook, demographics = synthetic_inputs()
    outcome = _build_processed_run(
        workbook_path=workbook,
        demographics_path=demographics,
        workspace_root=tmp_path / "workspace",
    )

    assert outcome.status == "passed"
    assert outcome.qc_error_count == 0
    assert outcome.table_row_counts == {
        "participants": 1,
        "participant_days": 2,
        "seizure_events": 3,
        "seizure_event_triggers": 6,
        "seizure_type_dictionary": 1,
        "form_responses": 0,
    }

    participants = _read_csv(outcome.output_directory / "participants.csv")
    assert [row["participant_id"] for row in participants] == ["SCN8A-010"]
    assert "fname" not in participants[0]
    assert "email" not in participants[0]
    assert "dob" not in participants[0]

    events = _read_csv(outcome.output_directory / "seizure_events.csv")
    assert len(events) == 3
    assert len({row["seizure_event_id"] for row in events}) == 3
    assert all(row["participant_id"] == "SCN8A-010" for row in events)
    assert all(row["reporting_lag_days"] == "1" for row in events)

    days = _read_csv(outcome.output_directory / "participant_days.csv")
    first_day = next(row for row in days if row["date"] == "2026-09-01")
    assert first_day["seizure_status"] == "reported_seizure"
    assert first_day["seizure_count"] == "3"
    assert first_day["sleep_quality_ordinal"] == "1"
    assert first_day["mood_rating"] == "red"
    assert first_day["mood_ordinal"] == "1"
    assert first_day["stool_bristol_type"] == "1"

def test_processed_run_builder_materializes_failed_qc_run(
    tmp_path: Path, synthetic_inputs
) -> None:
    workbook, demographics = synthetic_inputs(seizure_diary_count=2)
    outcome = _build_processed_run(
        workbook_path=workbook,
        demographics_path=demographics,
        workspace_root=tmp_path / "workspace",
    )

    assert outcome.status == "failed"
    assert outcome.qc_error_count > 0
    assert outcome.output_directory.parent.name == "workspace"
    qc = _read_csv(outcome.output_directory / "qc_results.csv")
    reconciliation = next(
        row for row in qc if row["check_id"] == "diary_observation_reconciliation"
    )
    assert reconciliation["status"] == "FAIL"


def test_manifest_contains_reproducibility_metadata(
    tmp_path: Path, synthetic_inputs
) -> None:
    workbook, demographics = synthetic_inputs()
    outcome = _build_processed_run(
        workbook_path=workbook,
        demographics_path=demographics,
        workspace_root=tmp_path / "workspace",
    )
    manifest = json.loads(
        (outcome.output_directory / "run_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "passed"
    assert manifest["source_extract_utc"] == "2026-09-02T20:30:00Z"
    assert manifest["analysis_as_of_date"] == "2026-09-02"
    assert len(manifest["source_sha256"]) == 64
    assert manifest["participant_id_corrections"] == {
        "SCN8A-0010": "SCN8A-010"
    }
    assert manifest["excluded_test_participant_ids"] == ["SCN8A-000"]
