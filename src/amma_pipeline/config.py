"""Study-specific processing rules and output schemas."""

from __future__ import annotations

from dataclasses import dataclass, field


SCHEMA_VERSION = "1.0.0"

REQUIRED_SHEETS = (
    "Extract info",
    "Participant Info",
    "Diary",
    "Observations",
    "Seizure Key",
    "Forms",
)

REQUIRED_COLUMNS = {
    "Participant Info": (
        "Participant ID",
        "Amma eDiary Start Date",
        "Status of patient",
    ),
    "Diary": (
        "Participant ID",
        "Date",
        "Observation Category",
        "Type of Day",
        "Mandatory Tracking Day",
        "Observation category count",
    ),
    "Observations": (
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
    ),
    "Seizure Key": (
        "Participant ID",
        "Seizure type",
        "Seizure description",
        "Archived",
    ),
    "Forms": (
        "Participant ID",
        "Questionnaire Name",
        "Form entry start date",
        "Form expiration date",
        "Date of Response",
        "Responded",
        "Question",
        "Response",
    ),
}

DEMOGRAPHIC_REQUIRED_COLUMNS = (
    "Participant ID",
    "sex",
    "dob",
    "country",
    "scn2a",
    "scn8a",
    "p_variant",
    "c_variant",
)

EXPECTED_DIARY_CATEGORIES = frozenset(
    {"SEIZURE", "RESCUE", "SLEEP", "IMPACT", "STOOL"}
)

PARTICIPANT_ID_CORRECTIONS = {"SCN8A-0010": "SCN8A-010"}
TEST_PARTICIPANT_IDS = frozenset({"SCN8A-000"})
MISSING_SENTINELS = frozenset({"115"})

SLEEP_ORDINAL = {"poor": 1, "fair": 2, "good": 3}
MOOD_NORMALIZATION = {
    "red": "red",
    "red_light": "red",
    "yellow": "yellow",
    "yellow_light": "yellow",
    "green": "green",
    "green_light": "green",
}
MOOD_ORDINAL = {"red": 1, "yellow": 2, "green": 3}

# The export's seven ordered labels correspond to Bristol types 1 through 7.
STOOL_BRISTOL_TYPE = {
    "severe_constipation": 1,
    "mild_constipation": 2,
    "lacking_fiber": 3,
    "normal_ideal_stool": 4,
    "normal": 5,
    "mild_diarrhea": 6,
    "severe_diarrhea": 7,
}


@dataclass(frozen=True)
class PipelineConfig:
    """Configuration that materially affects a processed dataset."""

    participant_id_corrections: dict[str, str] = field(
        default_factory=lambda: dict(PARTICIPANT_ID_CORRECTIONS)
    )
    test_participant_ids: frozenset[str] = TEST_PARTICIPANT_IDS
    missing_sentinels: frozenset[str] = MISSING_SENTINELS


def canonical_participant_id(value: object, config: PipelineConfig) -> str:
    """Return the approved canonical study participant ID."""

    participant_id = str(value or "").strip()
    return config.participant_id_corrections.get(participant_id, participant_id)


PARTICIPANT_FIELDS = (
    "participant_id",
    "app_start_date",
    "patient_status",
    "sex",
    "age_at_start_years",
    "country",
    "scn2a",
    "scn8a",
    "protein_variant",
    "coding_variant",
)

PARTICIPANT_DAY_FIELDS = (
    "participant_id",
    "date",
    "seizure_status",
    "seizure_count",
    "seizure_tracking_complete",
    "rescue_status",
    "rescue_count",
    "sleep_recorded",
    "sleep_observation_count",
    "sleep_quality",
    "sleep_quality_ordinal",
    "mood_recorded",
    "mood_observation_count",
    "mood_rating",
    "mood_ordinal",
    "stool_recorded",
    "stool_observation_count",
    "stool_category",
    "stool_bristol_type",
)

SEIZURE_EVENT_FIELDS = (
    "seizure_event_id",
    "participant_id",
    "observation_date",
    "seizure_type",
    "participant_seizure_description",
    "triggers_raw",
    "trigger_data_available",
    "entry_datetime_local",
    "entry_time_zone",
    "entry_datetime_utc",
    "reporting_lag_days",
    "source_sheet",
    "source_row_number",
)

TRIGGER_FIELDS = (
    "seizure_event_id",
    "participant_id",
    "observation_date",
    "trigger_position",
    "trigger_code",
)

SEIZURE_TYPE_FIELDS = (
    "participant_id",
    "seizure_type",
    "seizure_description",
    "archived",
)

FORM_RESPONSE_FIELDS = (
    "form_response_id",
    "participant_id",
    "questionnaire_name",
    "form_entry_start_date",
    "form_expiration_date",
    "response_date",
    "question",
    "response",
    "source_row_number",
)

QC_FIELDS = ("check_id", "severity", "status", "count", "message", "details")
