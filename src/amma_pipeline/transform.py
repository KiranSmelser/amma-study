"""Pure transformations from source records to analysis-ready tables."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable

from .config import (
    FORM_RESPONSE_FIELDS,
    MOOD_NORMALIZATION,
    MOOD_ORDINAL,
    PARTICIPANT_DAY_FIELDS,
    PARTICIPANT_FIELDS,
    SEIZURE_EVENT_FIELDS,
    SEIZURE_TYPE_FIELDS,
    SLEEP_ORDINAL,
    STOOL_BRISTOL_TYPE,
    TRIGGER_FIELDS,
    PipelineConfig,
    canonical_participant_id,
)
from .workbook_reader import WorkbookData


@dataclass(frozen=True)
class TransformResult:
    """Processed tables plus normalized source records used for QC."""

    tables: dict[str, list[dict[str, Any]]]
    normalized_sources: dict[str, list[dict[str, Any]]]
    invalid_demographic_dates: list[dict[str, Any]]


def _is_missing(value: object, config: PipelineConfig) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return not text or text in config.missing_sentinels


def _text(value: object, config: PipelineConfig, *, sentinel: bool = True) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if sentinel and text in config.missing_sentinels:
        return None
    return text


def _date_iso(value: object) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _datetime_iso(value: object) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).isoformat(sep=" ")
        except ValueError:
            continue
    return text


def _bool(value: object) -> bool | None:
    if value is None or str(value).strip() == "":
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    return None


def _integer(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _age_at(dob_iso: str | None, start_iso: str | None) -> float | None:
    if not dob_iso or not start_iso:
        return None
    dob = date.fromisoformat(dob_iso)
    start = date.fromisoformat(start_iso)
    if dob > start:
        return None
    return round((start - dob).days / 365.2425, 2)


def _normalize_source_records(
    records: Iterable[dict[str, Any]], config: PipelineConfig
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for record in records:
        copy = dict(record)
        copy["Participant ID"] = canonical_participant_id(
            copy.get("Participant ID"), config
        )
        normalized.append(copy)
    return normalized


def _stable_id(prefix: str, source_sha256: str, sheet: str, row: object) -> str:
    payload = f"{source_sha256}|{sheet}|{row}".encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:24]}"


def _reporting_lag(observation_date: str | None, entry_utc: str | None) -> int | None:
    if not observation_date or not entry_utc:
        return None
    try:
        return (date.fromisoformat(entry_utc[:10]) - date.fromisoformat(observation_date)).days
    except ValueError:
        return None


def _latest_category_value(
    rows: list[dict[str, Any]], field: str, config: PipelineConfig
) -> str | None:
    available = [row for row in rows if not _is_missing(row.get(field), config)]
    if not available:
        return None
    available.sort(
        key=lambda row: (
            str(row.get("Datetime of entry (UTC)") or ""),
            int(row.get("__source_row__") or 0),
        )
    )
    return _text(available[-1].get(field), config)


def transform(
    workbook: WorkbookData,
    demographics: list[dict[str, Any]],
    *,
    source_sha256: str,
    analysis_as_of_date: str,
    config: PipelineConfig | None = None,
) -> TransformResult:
    """Create analysis-ready tables from one complete cumulative snapshot."""

    cfg = config or PipelineConfig()
    normalized_sources = {
        name: _normalize_source_records(records, cfg)
        for name, records in workbook.tables.items()
    }
    normalized_demo = _normalize_source_records(demographics, cfg)
    normalized_sources["Demographics"] = normalized_demo

    participant_info = normalized_sources["Participant Info"]
    diary = normalized_sources["Diary"]
    observations = normalized_sources["Observations"]
    seizure_key = normalized_sources["Seizure Key"]
    forms = normalized_sources["Forms"]

    demo_by_id: dict[str, dict[str, Any]] = {}
    for row in normalized_demo:
        demo_by_id.setdefault(str(row["Participant ID"]), row)

    invalid_demographic_dates: list[dict[str, Any]] = []
    participants: list[dict[str, Any]] = []
    for row in participant_info:
        participant_id = str(row["Participant ID"])
        if participant_id in cfg.test_participant_ids:
            continue
        start_date = _date_iso(row.get("Amma eDiary Start Date"))
        demo = demo_by_id.get(participant_id, {})
        raw_dob = _text(demo.get("dob"), cfg)
        dob = _date_iso(raw_dob)
        if raw_dob and dob is None:
            invalid_demographic_dates.append(
                {
                    "participant_id": participant_id,
                    "field": "dob",
                    "source_row_number": demo.get("__source_row__"),
                }
            )
        participants.append(
            {
                "participant_id": participant_id,
                "app_start_date": start_date,
                "patient_status": _text(row.get("Status of patient"), cfg),
                "sex": _text(demo.get("sex"), cfg),
                "age_at_start_years": _age_at(dob, start_date),
                "country": _text(demo.get("country"), cfg),
                "scn2a": _bool(demo.get("scn2a")),
                "scn8a": _bool(demo.get("scn8a")),
                "protein_variant": _text(demo.get("p_variant"), cfg),
                "coding_variant": _text(demo.get("c_variant"), cfg),
            }
        )
    participants.sort(key=lambda row: row["participant_id"])

    included_ids = {row["participant_id"] for row in participants}
    observation_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        participant_id = str(row["Participant ID"])
        observation_date = _date_iso(row.get("Date of observation"))
        category = str(row.get("Observation category") or "").strip().upper()
        if observation_date:
            observation_groups[(participant_id, observation_date, category)].append(row)

    diary_groups: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in diary:
        participant_id = str(row["Participant ID"])
        diary_date = _date_iso(row.get("Date"))
        category = str(row.get("Observation Category") or "").strip().upper()
        if (
            participant_id in included_ids
            and diary_date
            and diary_date <= analysis_as_of_date
        ):
            diary_groups[(participant_id, diary_date)][category] = row

    def category_count(category_row: dict[str, Any] | None) -> int:
        return _integer((category_row or {}).get("Observation category count")) or 0

    def day_status(category_row: dict[str, Any] | None) -> str:
        raw = str((category_row or {}).get("Type of Day") or "").strip().lower()
        return {
            "missing": "missing",
            "no occurrence": "confirmed_none",
            "observations": "reported",
            "no optional data": "not_reported_optional",
        }.get(raw, "unknown")

    participant_days: list[dict[str, Any]] = []
    for (participant_id, diary_date), categories in sorted(diary_groups.items()):
        seizure_row = categories.get("SEIZURE")
        rescue_row = categories.get("RESCUE")
        seizure_raw_status = day_status(seizure_row)
        seizure_status = (
            "reported_seizure" if seizure_raw_status == "reported" else seizure_raw_status
        )

        sleep_rows = observation_groups.get((participant_id, diary_date, "SLEEP"), [])
        mood_rows = observation_groups.get((participant_id, diary_date, "IMPACT"), [])
        stool_rows = observation_groups.get((participant_id, diary_date, "STOOL"), [])
        sleep_quality = _latest_category_value(sleep_rows, "quality", cfg)
        raw_mood = _latest_category_value(mood_rows, "impact", cfg)
        mood_rating = MOOD_NORMALIZATION.get((raw_mood or "").lower())
        stool_category = _latest_category_value(stool_rows, "stool_scaling", cfg)

        participant_days.append(
            {
                "participant_id": participant_id,
                "date": diary_date,
                "seizure_status": seizure_status,
                "seizure_count": category_count(seizure_row),
                "seizure_tracking_complete": seizure_status
                in {"confirmed_none", "reported_seizure"},
                "rescue_status": day_status(rescue_row),
                "rescue_count": category_count(rescue_row),
                "sleep_recorded": bool(sleep_rows),
                "sleep_observation_count": len(sleep_rows),
                "sleep_quality": sleep_quality,
                "sleep_quality_ordinal": SLEEP_ORDINAL.get(
                    (sleep_quality or "").lower()
                ),
                "mood_recorded": bool(mood_rows),
                "mood_observation_count": len(mood_rows),
                "mood_rating": mood_rating,
                "mood_ordinal": MOOD_ORDINAL.get(mood_rating or ""),
                "stool_recorded": bool(stool_rows),
                "stool_observation_count": len(stool_rows),
                "stool_category": stool_category,
                "stool_bristol_type": STOOL_BRISTOL_TYPE.get(
                    (stool_category or "").lower()
                ),
            }
        )

    seizure_events: list[dict[str, Any]] = []
    trigger_rows: list[dict[str, Any]] = []
    for row in observations:
        participant_id = str(row["Participant ID"])
        if participant_id not in included_ids:
            continue
        category = str(row.get("Observation category") or "").strip().upper()
        observation_date = _date_iso(row.get("Date of observation"))
        if category != "SEIZURE" or not observation_date or observation_date > analysis_as_of_date:
            continue

        source_row = row.get("__source_row__")
        event_id = _stable_id("seiz", source_sha256, "Observations", source_row)
        triggers_raw = _text(row.get("triggers"), cfg)
        entry_utc = _datetime_iso(row.get("Datetime of entry (UTC)"))
        seizure_events.append(
            {
                "seizure_event_id": event_id,
                "participant_id": participant_id,
                "observation_date": observation_date,
                "seizure_type": _text(row.get("Seizure Type"), cfg),
                "participant_seizure_description": _text(
                    row.get("Participant seizure description"), cfg
                ),
                "triggers_raw": triggers_raw,
                "trigger_data_available": triggers_raw is not None,
                "entry_datetime_local": _datetime_iso(
                    row.get("Datetime of entry (local)")
                ),
                "entry_time_zone": _text(row.get("Time zone of entry"), cfg),
                "entry_datetime_utc": entry_utc,
                "reporting_lag_days": _reporting_lag(observation_date, entry_utc),
                "source_sheet": "Observations",
                "source_row_number": source_row,
            }
        )
        if triggers_raw:
            for position, trigger in enumerate(triggers_raw.split(","), start=1):
                trigger_code = trigger.strip()
                if trigger_code:
                    trigger_rows.append(
                        {
                            "seizure_event_id": event_id,
                            "participant_id": participant_id,
                            "observation_date": observation_date,
                            "trigger_position": position,
                            "trigger_code": trigger_code,
                        }
                    )

    seizure_types: list[dict[str, Any]] = []
    for row in seizure_key:
        participant_id = str(row["Participant ID"])
        if participant_id not in included_ids:
            continue
        seizure_types.append(
            {
                "participant_id": participant_id,
                "seizure_type": _text(row.get("Seizure type"), cfg),
                "seizure_description": _text(row.get("Seizure description"), cfg),
                "archived": _bool(row.get("Archived")),
            }
        )
    seizure_types.sort(key=lambda row: (row["participant_id"], row["seizure_type"] or ""))

    form_responses: list[dict[str, Any]] = []
    for row in forms:
        participant_id = str(row["Participant ID"])
        if participant_id not in included_ids:
            continue
        if _bool(row.get("Responded")) is not True or _is_missing(row.get("Response"), cfg):
            continue
        source_row = row.get("__source_row__")
        form_responses.append(
            {
                "form_response_id": _stable_id(
                    "form", source_sha256, "Forms", source_row
                ),
                "participant_id": participant_id,
                "questionnaire_name": _text(row.get("Questionnaire Name"), cfg),
                "form_entry_start_date": _date_iso(row.get("Form entry start date")),
                "form_expiration_date": _date_iso(row.get("Form expiration date")),
                "response_date": _date_iso(row.get("Date of Response")),
                "question": _text(row.get("Question"), cfg),
                "response": _text(row.get("Response"), cfg),
                "source_row_number": source_row,
            }
        )

    tables = {
        "participants": [{key: row.get(key) for key in PARTICIPANT_FIELDS} for row in participants],
        "participant_days": [
            {key: row.get(key) for key in PARTICIPANT_DAY_FIELDS}
            for row in participant_days
        ],
        "seizure_events": [
            {key: row.get(key) for key in SEIZURE_EVENT_FIELDS}
            for row in seizure_events
        ],
        "seizure_event_triggers": [
            {key: row.get(key) for key in TRIGGER_FIELDS} for row in trigger_rows
        ],
        "seizure_type_dictionary": [
            {key: row.get(key) for key in SEIZURE_TYPE_FIELDS}
            for row in seizure_types
        ],
        "form_responses": [
            {key: row.get(key) for key in FORM_RESPONSE_FIELDS}
            for row in form_responses
        ],
    }
    return TransformResult(
        tables=tables,
        normalized_sources=normalized_sources,
        invalid_demographic_dates=invalid_demographic_dates,
    )
