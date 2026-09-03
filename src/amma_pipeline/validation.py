"""Required QC checks for an Amma preprocessing run."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from .config import (
    EXPECTED_DIARY_CATEGORIES,
    MOOD_NORMALIZATION,
    SLEEP_ORDINAL,
    STOOL_BRISTOL_TYPE,
    PipelineConfig,
)
from .transform import TransformResult


@dataclass(frozen=True)
class QCResult:
    check_id: str
    severity: str
    status: str
    count: int
    message: str
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _date_iso(value: object) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _count(value: object) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _details(values: Iterable[Any], limit: int = 20) -> str:
    materialized = list(values)
    return json.dumps(materialized[:limit], sort_keys=True, default=str)


def _result(
    check_id: str,
    severity: str,
    problems: list[Any],
    pass_message: str,
    fail_message: str,
) -> QCResult:
    if not problems:
        return QCResult(check_id, severity, "PASS", 0, pass_message)
    status = "FAIL" if severity == "ERROR" else "WARN"
    return QCResult(
        check_id,
        severity,
        status,
        len(problems),
        fail_message,
        _details(problems),
    )


def validate(
    transformed: TransformResult,
    *,
    analysis_as_of_date: str,
    config: PipelineConfig | None = None,
) -> list[QCResult]:
    """Run deterministic structural and semantic checks."""

    cfg = config or PipelineConfig()
    sources = transformed.normalized_sources
    participant_info = sources["Participant Info"]
    demo = sources["Demographics"]
    diary = sources["Diary"]
    observations = sources["Observations"]
    seizure_key = sources["Seizure Key"]

    results: list[QCResult] = []

    participant_counts = Counter(str(row["Participant ID"]) for row in participant_info)
    duplicate_participants = [key for key, count in participant_counts.items() if count > 1]
    results.append(
        _result(
            "participant_info_unique",
            "ERROR",
            duplicate_participants,
            "Participant Info has one row per canonical participant ID.",
            "Canonical participant IDs are duplicated in Participant Info.",
        )
    )

    demo_counts = Counter(str(row["Participant ID"]) for row in demo)
    duplicate_demo = [key for key, count in demo_counts.items() if count > 1]
    results.append(
        _result(
            "demographics_unique",
            "ERROR",
            duplicate_demo,
            "Demographics has one row per canonical participant ID.",
            "Canonical participant IDs are duplicated in demographics.",
        )
    )

    participant_ids = set(participant_counts)
    source_reference_problems: list[dict[str, Any]] = []
    for source_name in ("Diary", "Observations", "Seizure Key", "Forms"):
        for row in sources[source_name]:
            participant_id = str(row["Participant ID"])
            if participant_id not in participant_ids:
                source_reference_problems.append(
                    {
                        "source": source_name,
                        "row": row.get("__source_row__"),
                        "participant_id": participant_id,
                    }
                )
    results.append(
        _result(
            "participant_referential_integrity",
            "ERROR",
            source_reference_problems,
            "All source records reference a Participant Info ID.",
            "Source records reference unknown participant IDs.",
        )
    )

    missing_demo = sorted(
        participant_ids - set(demo_counts) - set(cfg.test_participant_ids)
    )
    results.append(
        _result(
            "participants_have_demographics",
            "ERROR",
            missing_demo,
            "Every non-test participant has a demographic record.",
            "Non-test participants are missing demographic records.",
        )
    )

    demo_without_export = sorted(set(demo_counts) - participant_ids)
    results.append(
        _result(
            "demographics_without_app_export",
            "WARNING",
            demo_without_export,
            "Every demographic participant is present in this app export.",
            "Some demographic participants are not present in this app export.",
        )
    )

    diary_key_counts: Counter[tuple[str, str | None, str]] = Counter()
    for row in diary:
        diary_date = _date_iso(row.get("Date"))
        if diary_date and diary_date <= analysis_as_of_date:
            diary_key_counts[
                (
                    str(row["Participant ID"]),
                    diary_date,
                    str(row.get("Observation Category") or "").strip().upper(),
                )
            ] += 1
    duplicate_diary_keys = [key for key, count in diary_key_counts.items() if count > 1]
    results.append(
        _result(
            "diary_key_unique",
            "ERROR",
            duplicate_diary_keys,
            "Diary participant/date/category keys are unique.",
            "Diary contains duplicate participant/date/category keys.",
        )
    )

    unexpected_diary_categories = [
        {
            "row": row.get("__source_row__"),
            "category": str(row.get("Observation Category") or "").strip().upper(),
        }
        for row in diary
        if str(row.get("Observation Category") or "").strip().upper()
        not in EXPECTED_DIARY_CATEGORIES
    ]
    results.append(
        _result(
            "diary_categories_known",
            "ERROR",
            unexpected_diary_categories,
            "Diary contains only the five documented observation categories.",
            "Diary contains unexpected observation categories.",
        )
    )

    expected_keys: set[tuple[str, str, str]] = set()
    cutoff = date.fromisoformat(analysis_as_of_date)
    for row in participant_info:
        participant_id = str(row["Participant ID"])
        if participant_id in cfg.test_participant_ids:
            continue
        start_iso = _date_iso(row.get("Amma eDiary Start Date"))
        if not start_iso:
            continue
        current = date.fromisoformat(start_iso)
        while current <= cutoff:
            for category in EXPECTED_DIARY_CATEGORIES:
                expected_keys.add((participant_id, current.isoformat(), category))
            current += timedelta(days=1)
    actual_non_test_keys = {
        key for key in diary_key_counts if key[0] not in cfg.test_participant_ids
    }
    missing_diary_keys = sorted(expected_keys - actual_non_test_keys)
    results.append(
        _result(
            "daily_grid_complete",
            "ERROR",
            missing_diary_keys,
            "The five-category daily grid is complete through the analysis cutoff.",
            "Expected participant/date/category rows are missing from Diary.",
        )
    )

    invalid_status_counts: list[dict[str, Any]] = []
    invalid_status_domains: list[dict[str, Any]] = []
    allowed_statuses = {
        "SEIZURE": {"Missing", "No occurrence", "Observations"},
        "RESCUE": {"Missing", "No occurrence", "Observations"},
        "SLEEP": {"No optional data", "Observations"},
        "IMPACT": {"No optional data", "Observations"},
        "STOOL": {"No optional data", "Observations"},
    }
    for row in diary:
        diary_date = _date_iso(row.get("Date"))
        if not diary_date or diary_date > analysis_as_of_date:
            continue
        day_type = str(row.get("Type of Day") or "").strip()
        category = str(row.get("Observation Category") or "").strip().upper()
        count = _count(row.get("Observation category count"))
        if category in allowed_statuses and day_type not in allowed_statuses[category]:
            invalid_status_domains.append(
                {
                    "row": row.get("__source_row__"),
                    "category": category,
                    "type_of_day": day_type,
                }
            )
        if count is None or count < 0:
            invalid_status_counts.append(
                {"row": row.get("__source_row__"), "reason": "invalid count"}
            )
        elif day_type in {"Missing", "No occurrence", "No optional data"} and count != 0:
            invalid_status_counts.append(
                {"row": row.get("__source_row__"), "reason": "nonzero absent count"}
            )
        elif day_type == "Observations" and count < 1:
            invalid_status_counts.append(
                {"row": row.get("__source_row__"), "reason": "empty observations"}
            )
    results.append(
        _result(
            "diary_status_count_consistent",
            "ERROR",
            invalid_status_counts,
            "Diary status labels are consistent with category counts.",
            "Diary status labels and category counts disagree.",
        )
    )
    results.append(
        _result(
            "diary_status_domains",
            "ERROR",
            invalid_status_domains,
            "Diary status labels follow the required and optional category rules.",
            "Diary contains a status that is invalid for its category.",
        )
    )

    observation_counts: Counter[tuple[str, str | None, str]] = Counter()
    for row in observations:
        observation_date = _date_iso(row.get("Date of observation"))
        if observation_date and observation_date <= analysis_as_of_date:
            observation_counts[
                (
                    str(row["Participant ID"]),
                    observation_date,
                    str(row.get("Observation category") or "").strip().upper(),
                )
            ] += 1
    reconciliation_problems: list[dict[str, Any]] = []
    for row in diary:
        diary_date = _date_iso(row.get("Date"))
        if not diary_date or diary_date > analysis_as_of_date:
            continue
        key = (
            str(row["Participant ID"]),
            diary_date,
            str(row.get("Observation Category") or "").strip().upper(),
        )
        diary_count = _count(row.get("Observation category count"))
        if diary_count != observation_counts.get(key, 0):
            reconciliation_problems.append(
                {
                    "participant_id": key[0],
                    "date": key[1],
                    "category": key[2],
                    "diary_count": diary_count,
                    "observation_count": observation_counts.get(key, 0),
                }
            )
    results.append(
        _result(
            "diary_observation_reconciliation",
            "ERROR",
            reconciliation_problems,
            "Every Diary count reconciles to observation rows.",
            "Diary counts do not reconcile to observation rows.",
        )
    )

    observations_without_diary = [
        {
            "participant_id": key[0],
            "date": key[1],
            "category": key[2],
            "observation_count": count,
        }
        for key, count in observation_counts.items()
        if key not in diary_key_counts
    ]
    results.append(
        _result(
            "observations_have_diary_row",
            "ERROR",
            observations_without_diary,
            "Every observation group has a corresponding Diary row.",
            "Observation groups exist without a corresponding Diary row.",
        )
    )

    mandatory_problems: list[dict[str, Any]] = []
    for row in diary:
        category = str(row.get("Observation Category") or "").strip().upper()
        day_type = str(row.get("Type of Day") or "").strip()
        mandatory = str(row.get("Mandatory Tracking Day") or "").strip()
        expected: str | None = None
        if category == "SEIZURE":
            expected = "Yes"
        elif category in {"SLEEP", "IMPACT", "STOOL"}:
            expected = "Yes" if day_type == "Observations" else "No"
        if expected and mandatory != expected:
            mandatory_problems.append(
                {
                    "row": row.get("__source_row__"),
                    "category": category,
                    "type_of_day": day_type,
                    "observed": mandatory,
                    "expected": expected,
                }
            )
    results.append(
        _result(
            "mandatory_tracking_semantics",
            "ERROR",
            mandatory_problems,
            "Mandatory Tracking Day follows the inferred required/optional rules.",
            "Mandatory Tracking Day violates the inferred required/optional rules.",
        )
    )

    invalid_values: list[dict[str, Any]] = []
    for row in observations:
        category = str(row.get("Observation category") or "").strip().upper()
        if category == "SLEEP":
            value = str(row.get("quality") or "").strip().lower()
            if value not in SLEEP_ORDINAL:
                invalid_values.append({"row": row.get("__source_row__"), "field": "quality", "value": value})
        elif category == "IMPACT":
            value = str(row.get("impact") or "").strip().lower()
            if value not in MOOD_NORMALIZATION:
                invalid_values.append({"row": row.get("__source_row__"), "field": "impact", "value": value})
        elif category == "STOOL":
            value = str(row.get("stool_scaling") or "").strip().lower()
            if value not in STOOL_BRISTOL_TYPE:
                invalid_values.append({"row": row.get("__source_row__"), "field": "stool_scaling", "value": value})
    results.append(
        _result(
            "optional_value_domains",
            "ERROR",
            invalid_values,
            "Sleep, mood, and stool observations use documented categories.",
            "Unexpected sleep, mood, or stool category values were found.",
        )
    )

    optional_group_counts = Counter()
    for row in observations:
        category = str(row.get("Observation category") or "").strip().upper()
        observation_date = _date_iso(row.get("Date of observation"))
        if category in {"SLEEP", "IMPACT", "STOOL"} and observation_date:
            optional_group_counts[
                (str(row["Participant ID"]), observation_date, category)
            ] += 1
    repeated_optional_groups = [
        {
            "participant_id": key[0],
            "date": key[1],
            "category": key[2],
            "observation_count": count,
        }
        for key, count in optional_group_counts.items()
        if count > 1
    ]
    results.append(
        _result(
            "single_optional_observation_per_day",
            "WARNING",
            repeated_optional_groups,
            "Each optional category has at most one observation per participant-day.",
            "Some optional categories have multiple observations; participant_days uses the latest submitted value.",
        )
    )

    configured_types = {
        (str(row["Participant ID"]), str(row.get("Seizure type") or "").strip())
        for row in seizure_key
    }
    unknown_seizure_types = []
    for row in observations:
        if str(row.get("Observation category") or "").strip().upper() != "SEIZURE":
            continue
        key = (
            str(row["Participant ID"]),
            str(row.get("Seizure Type") or "").strip(),
        )
        if key not in configured_types:
            unknown_seizure_types.append(
                {"row": row.get("__source_row__"), "participant_id": key[0], "seizure_type": key[1]}
            )
    results.append(
        _result(
            "seizure_types_configured",
            "ERROR",
            unknown_seizure_types,
            "Every seizure observation uses a participant-configured seizure type.",
            "Seizure observations contain unconfigured seizure types.",
        )
    )

    seizure_signatures = Counter(
        tuple((key, str(value)) for key, value in row.items() if key != "__source_row__")
        for row in observations
        if str(row.get("Observation category") or "").strip().upper() == "SEIZURE"
    )
    duplicate_seizure_extras = sum(count - 1 for count in seizure_signatures.values() if count > 1)
    results.append(
        QCResult(
            "identical_seizure_rows_retained",
            "INFO",
            "PASS",
            duplicate_seizure_extras,
            "Identical seizure rows are intentionally retained as distinct seizures.",
        )
    )

    results.append(
        _result(
            "demographic_dates_parseable",
            "WARNING",
            transformed.invalid_demographic_dates,
            "All supplied demographic dates are parseable.",
            "Some supplied demographic dates are not parseable and derived ages are missing.",
        )
    )

    results.append(
        QCResult(
            "event_time_semantics_pending",
            "INFO",
            "INFO",
            0,
            "Seizure occurrence time remains unresolved with Amma; entry timestamps are provenance only.",
        )
    )
    return results


def error_count(results: Iterable[QCResult]) -> int:
    return sum(result.count for result in results if result.severity == "ERROR" and result.status == "FAIL")


def warning_count(results: Iterable[QCResult]) -> int:
    return sum(result.count for result in results if result.severity == "WARNING" and result.status == "WARN")
