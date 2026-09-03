"""Semantic descriptions for the published processed tables."""

from __future__ import annotations


DATA_DICTIONARY = """# Amma processed-data dictionary

## General conventions

- One source workbook is treated as one complete cumulative snapshot.
- `SCN8A-0010` is normalized to the canonical ID `SCN8A-010`.
- Test participant `SCN8A-000` is excluded from processed analytical tables.
- Source value `115` means unavailable/not applicable and is written as missing.
- Identical seizure rows are retained because they represent multiple seizures.
- Dates use ISO `YYYY-MM-DD`; entry datetimes are provenance, not occurrence times.

## participants.csv

One row per non-test participant. Direct identifiers, contact information, exact
date of birth, city, and state are excluded. `age_at_start_years` is derived from
date of birth and app start date when both are parseable.

## participant_days.csv

One row per non-test participant and expected diary date. `seizure_status` is one
of `reported_seizure`, `confirmed_none`, `missing`, or `unknown`.
`seizure_tracking_complete` is true only for reported seizure or explicitly
confirmed seizure-free days. Sleep, mood, and stool are optional measures;
absence is not treated as protocol noncompliance.

Sleep and mood ordinal values run from 1 (worst) to 3 (best). Mood export values
`red_light`, `yellow_light`, and `green_light` are normalized to `red`, `yellow`,
and `green`. Stool categories map in order to Bristol Stool Chart types 1–7.
If an optional category has multiple observations on one day, the day table uses
the latest submitted value and QC reports the repeated group; the observation
count remains visible.

## seizure_events.csv

One row per source seizure observation. `seizure_event_id` is a reproducible
technical source-row identifier, not an Amma-provided clinical event ID. Entry
timestamps indicate submission time. `reporting_lag_days` is the difference
between entry UTC date and observation date.

## seizure_event_triggers.csv

One row per seizure-event/trigger selection. `unknown` remains an explicit value;
missing trigger data produces no child row and is flagged in `seizure_events.csv`.

## seizure_type_dictionary.csv

Participant-specific configured seizure types and participant descriptions.

## form_responses.csv

Only actual, non-sentinel questionnaire responses. Pre-generated unanswered form
schedules are not included.

## qc_results.csv and run_manifest.json

Machine-readable validation results and full run provenance. A run with any
required QC failure is placed under `failed/` and cannot update `latest.json`.
"""
