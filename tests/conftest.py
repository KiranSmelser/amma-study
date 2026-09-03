from __future__ import annotations

import csv
from pathlib import Path

import pytest
from openpyxl import Workbook


def _add_sheet(workbook: Workbook, name: str, rows: list[list[object]]) -> None:
    sheet = workbook.create_sheet(name)
    for row in rows:
        sheet.append(row)


@pytest.fixture
def synthetic_inputs(tmp_path: Path):
    def build(*, seizure_diary_count: int = 3) -> tuple[Path, Path]:
        workbook_path = tmp_path / f"amma-{seizure_diary_count}.xlsx"
        workbook = Workbook()
        workbook.remove(workbook.active)

        _add_sheet(
            workbook,
            "Extract info",
            [
                ["Extract date (UTC)", "2026-09-02 20:30:00"],
                ["Requested by", "study@example.edu"],
                ["Study", "Synthetic Amma Study"],
            ],
        )
        _add_sheet(
            workbook,
            "Participant Info",
            [
                ["Participant ID", "Amma eDiary Start Date", "Status of patient"],
                ["SCN8A-000", "2026-09-01", "ACTIVE"],
                ["SCN8A-0010", "2026-09-01", "ACTIVE"],
            ],
        )

        diary_header = [
            "Participant ID",
            "Date",
            "Observation Category",
            "Type of Day",
            "Mandatory Tracking Day",
            "Observation category count",
        ]
        diary_rows = [diary_header]
        for participant_id in ("SCN8A-000", "SCN8A-0010"):
            for diary_date in ("2026-09-01", "2026-09-02"):
                is_analysis_participant = participant_id == "SCN8A-0010"
                is_first_day = diary_date == "2026-09-01"
                seizure_count = (
                    seizure_diary_count if is_analysis_participant and is_first_day else 0
                )
                diary_rows.extend(
                    [
                        [
                            participant_id,
                            diary_date,
                            "SEIZURE",
                            "Observations" if seizure_count else "No occurrence",
                            "Yes",
                            seizure_count,
                        ],
                        [participant_id, diary_date, "RESCUE", "No occurrence", "Yes", 0],
                        [
                            participant_id,
                            diary_date,
                            "SLEEP",
                            "Observations" if is_analysis_participant and is_first_day else "No optional data",
                            "Yes" if is_analysis_participant and is_first_day else "No",
                            1 if is_analysis_participant and is_first_day else 0,
                        ],
                        [
                            participant_id,
                            diary_date,
                            "IMPACT",
                            "Observations" if is_analysis_participant and is_first_day else "No optional data",
                            "Yes" if is_analysis_participant and is_first_day else "No",
                            1 if is_analysis_participant and is_first_day else 0,
                        ],
                        [
                            participant_id,
                            diary_date,
                            "STOOL",
                            "Observations" if is_analysis_participant and is_first_day else "No optional data",
                            "Yes" if is_analysis_participant and is_first_day else "No",
                            1 if is_analysis_participant and is_first_day else 0,
                        ],
                    ]
                )
        _add_sheet(workbook, "Diary", diary_rows)

        observation_header = [
            "Participant ID",
            "Date of observation",
            "Datetime of entry (local)",
            "Time zone of entry",
            "Datetime of entry (UTC)",
            "Observation category",
            "Seizure Type",
            "Participant seizure description",
            "triggers",
            "cluster_duration_minutes",
            "unclassified_description_checklist",
            "rescue_medication_occurred",
            "quality",
            "impact",
            "stool_scaling",
        ]
        seizure_row = [
            "SCN8A-0010",
            "2026-09-01",
            "2026-09-02 09:00:00",
            "Eastern Time",
            "2026-09-02 13:00:00",
            "SEIZURE",
            "Tonic",
            "Tonic",
            "tired, lack_of_sleep",
            "115",
            "115",
            "115",
            "115",
            "115",
            "115",
        ]
        observations = [observation_header, seizure_row, seizure_row, seizure_row]
        observations.extend(
            [
                ["SCN8A-0010", "2026-09-01", "2026-09-02 09:01:00", "Eastern Time", "2026-09-02 13:01:00", "SLEEP", "115", "115", "115", "115", "115", "115", "poor", "115", "115"],
                ["SCN8A-0010", "2026-09-01", "2026-09-02 09:02:00", "Eastern Time", "2026-09-02 13:02:00", "IMPACT", "115", "115", "115", "115", "115", "115", "115", "red_light", "115"],
                ["SCN8A-0010", "2026-09-01", "2026-09-02 09:03:00", "Eastern Time", "2026-09-02 13:03:00", "STOOL", "115", "115", "115", "115", "115", "115", "115", "115", "severe_constipation"],
            ]
        )
        _add_sheet(workbook, "Observations", observations)

        _add_sheet(
            workbook,
            "Seizure Key",
            [
                ["Participant ID", "Seizure type", "Seizure description", "Archived"],
                ["SCN8A-000", "Unclassified", "Test", "no"],
                ["SCN8A-0010", "Tonic", "Tonic", "no"],
            ],
        )
        _add_sheet(
            workbook,
            "Forms",
            [
                ["Participant ID", "Questionnaire Name", "Form entry start date", "Form expiration date", "Date of Response", "Responded", "Question", "Response"],
                ["SCN8A-000", "Test form", "2026-09-01", "2026-09-08", None, "No", "Question", "115"],
            ],
        )
        workbook.save(workbook_path)

        demographics_path = tmp_path / "demo.csv"
        fieldnames = [
            "Participant ID",
            "fname",
            "lname",
            "email",
            "sex",
            "dob",
            "country",
            "scn2a",
            "scn8a",
            "p_variant",
            "c_variant",
        ]
        with demographics_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(
                [
                    {"Participant ID": "SCN8A-000", "fname": "Test", "lname": "User", "email": "test@example.org", "sex": "", "dob": "", "country": "", "scn2a": "0", "scn8a": "1", "p_variant": "", "c_variant": ""},
                    {"Participant ID": "SCN8A-010", "fname": "Synthetic", "lname": "Participant", "email": "synthetic@example.org", "sex": "female", "dob": "01/01/2020", "country": "USA", "scn2a": "0", "scn8a": "1", "p_variant": "Example", "c_variant": "Example"},
                    {"Participant ID": "SCN8A-011", "fname": "Not", "lname": "Started", "email": "notstarted@example.org", "sex": "male", "dob": "01/01/2021", "country": "USA", "scn2a": "0", "scn8a": "1", "p_variant": "Example2", "c_variant": "Example2"},
                ]
            )
        return workbook_path, demographics_path

    return build
