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

## Monthly patient graphics (R)

After a passing preprocessing run, generate one PDF per patient per calendar
month using the existing processed CSVs:

```bash
Rscript -e 'install.packages(c("ggplot2", "dplyr", "tidyr", "readr", "jsonlite", "patchwork"), repos="https://cloud.r-project.org")'
Rscript scripts/monthly_patient_graphics.R data/processed data/reports/monthly data/demo.csv
```

The R step resolves `latest.json`, verifies the source run passed QC, and writes
`data/reports/monthly/<run_id>/<participant_name>/<participant_name>_YYYY-MM.pdf`.
Names come from `fname` and `lname` in the demographics CSV (third argument;
default `data/demo.csv`). Missing names and duplicate filename names stop report
generation. Spaces become underscores; path-unsafe characters are replaced.
The chart title also uses the participant name.
A `report_index.csv` lists outputs and their source run. It does not modify the
processed run. This is a separate reporting command; the Python CLI does not
invoke it automatically.

Each PDF has days 1–15 and 16–month end with the same seizure-count scale.
The reference-inspired design uses rounded light-gray tracks, colored stacks,
contrasting segment counts where space permits, purple daily seizure counts, and a colored-dot
legend. Gray track space is a visual background, not a target or missing seizure
category. Each full track represents the maximum daily count in that patient-month
(or 1 for a month with no reported seizures). This scale is not labeled on the graphic.
Stacks count rows from `seizure_events.csv`, including repeated observations,
and reconcile to `participant_days.csv`. A missing seizure type gets its own
“Unspecified type” category. Colors are defined directly in the R script; no separate color CSV is written.
Type labels use the exported seizure type, not patient-specific free-text
seizure descriptions.

Sleep is the recorded quality (red poor, yellow fair, green good), **not hours**. Impact uses
the normalized source categories (`mood_rating`: red, yellow, green), **not a new 1–5
scale**. Both use the processed observation date and latest submitted daily
value; sleep is not shifted to another date. Seizure-free days show 0, missing
days —, unknown days —, and dates without a diary row —. Missing optional
sleep/mood values show —. Only months represented in the patient-day table are
rendered; the script does not extrapolate enrollment or fabricate diary days.
Scales adapt by patient-month, so compare counts rather than bar heights between
separate images. Generated reports are excluded from Git.

Run the synthetic reporting checks from the repository root:

```bash
Rscript tests/test_monthly_patient_graphics.R
```

Seizure colors use exact hex values from the supplied Epilepsia Open palette:
Absence `#00545E`, Focal (no observable sign) `#A30234`, Focal Motor `#677719`,
Tonic `#0076C0`, and Tonic-Clonic `#7A5071`. Additional types receive unused
colors from that same palette in sorted type order within a run. To keep a new
type stable across changing datasets, add it to `fixed_colors` in the script. Segment
labels use light or dark text according to background luminance.

Sleep and impact appear as Amma-style red (`#FF0000`), yellow (`#FFC000`), or
green (`#008000`) circles. Impact measures seizure-related function, not mood:
red means unable to function for all or most of the day; yellow means function
partly reduced; green means able to function with no seizure impact. The source
column remains `mood_rating` for compatibility. Missing values remain dashes.

Graphic headings capitalize participant names. The Seizures row uses a dash for
missing, unknown, or absent diary entries, and 0 for confirmed seizure-free days.
The graphics omit bottom footnotes; scale definitions remain documented here.
