# Amma Study Pipeline

This repository contains the reproducible preprocessing pipeline for the Amma
seizure-diary study. Study data and credentials are intentionally excluded from
Git.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install '.[dev]'
```

## Process a local export

```bash
amma-pipeline process \
  --input "/path/to/amma-export.xlsx" \
  --demographics "/path/to/demo.csv" \
  --output-root "data/processed"
```

The pipeline treats each workbook as a complete cumulative snapshot. A passing
run is written to `data/processed/runs/<run_id>/` and becomes the target of
`data/processed/latest.json`. A run with required QC failures is written under
`data/processed/failed/` and never updates `latest.json`.

The main processed tables are:

- `participants.csv`
- `participant_days.csv`
- `seizure_events.csv`
- `seizure_event_triggers.csv`
- `seizure_type_dictionary.csv`
- `form_responses.csv`
- `qc_results.csv`
- `run_manifest.json`
- `data_dictionary.md`

`SCN8A-0010` is normalized to `SCN8A-010`, `SCN8A-000` is excluded as a test
participant, the documented `115` sentinel becomes missing, and identical
seizure rows are retained as distinct reported seizures.
