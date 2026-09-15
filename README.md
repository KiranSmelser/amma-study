# Amma Study Pipeline

This repository contains the reproducible, Box-backed preprocessing and patient
reporting pipeline for the Amma seizure-diary study. Study data and credentials
are intentionally excluded from Git. Box is the durable system of record; the
pipeline uses private operating-system temporary directories and does not retain
raw, identifiable, processed, or report data in the repository.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install '.[dev]'
```

## Verify Box access

After configuring `.env` and storing the client secret in the macOS login
Keychain under service `amma-study-box-client-secret` and account `amma-study`,
run the permission smoke test:

```bash
python scripts/box_smoke_test.py
```

The test authenticates as the Amma service account, verifies access to the five
configured folders, exercises upload/download/checksum/delete round trips in
`Processed` and `Patient Reports`, confirms that the other folders reject
writes, and checks root isolation. It never prints credentials, Box item names,
or file contents. Its synthetic test uploads are permanently purged even after
most failures. The raw download check is reported as skipped when `Raw App
Exports` contains no file.

## Process and publish from Box

Place each immutable cumulative Amma `.xlsx` export directly in `Raw App
Exports` and the restricted demographics `.csv` in `Identifiers`. Never replace
an older raw export with a new Box version. List the available explicit input
IDs with:

```bash
amma-pipeline box-inputs --env-file .env
```

Process one explicitly selected pair of Box files with:

```bash
amma-pipeline process-box \
  --workbook-file-id BOX_RAW_FILE_ID \
  --demographics-file-id BOX_DEMOGRAPHICS_FILE_ID \
  --env-file .env
```

File IDs are required so the pipeline never guesses which upload is latest.
Both inputs must be direct children of their configured authorized folders. The
command creates an access-restricted temporary workspace, verifies downloads
against Box's size and SHA-1 metadata, preprocesses the data, publishes and
verifies the complete run in Box, and then removes the workspace. It does not
create or retain a repository `data/` directory.

Box publication first uploads all outputs into a uniquely named staging folder
under `Processed`. Every size and SHA-1 is checked, `run_manifest.json` is
augmented with Box input/output IDs, versions, and checksums, and the complete
staging folder is then moved to either `runs/<run_id>` or `failed/<run_id>`.
Passing runs update `Processed/latest.json` using Box file versioning; failed-QC
runs never update it. Existing run IDs are never overwritten. An interrupted
pre-publication upload is cleaned up without changing `latest.json`.

The command exits `0` for a passing run, `2` for a published failed-QC run, and
`1` for an operational error. Temporary files are removed on each handled exit;
an operational error never silently claims a successful Box publication.

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

## Generate and publish monthly patient reports

Install the R dependencies once if they are not already available:

```bash
Rscript -e 'install.packages(c("ggplot2", "dplyr", "tidyr", "readr", "jsonlite", "patchwork"), repos="https://cloud.r-project.org")'
```

Generate one PDF per participant per calendar month from an explicit processed
Box run and demographics file, then validate and publish the report set:

```bash
amma-pipeline generate-reports-box \
  --source-run-id SOURCE_RUN_ID \
  --demographics-file-id BOX_DEMOGRAPHICS_FILE_ID \
  --env-file .env
```

The command downloads and verifies the complete passing source run and exact
demographics version into a private temporary workspace. Names from `fname` and
`lname` appear only inside PDF titles, never in Box paths or the Box-safe report
index. Missing names and unsafe participant IDs stop report generation.
`report_index.csv` contains only participant ID, month, source run, and relative
file path.

The report-set fingerprint and manifest include the processed run, report schema,
reporting-script SHA-256, pipeline version, and demographics file ID, version,
Box SHA-1, and downloaded SHA-256. Publication uses a staging folder under
`Patient Reports`, verifies every upload, moves the complete set to
`Patient Reports/runs/<report_set_id>`, and only then updates
`Patient Reports/latest.json`. Existing report sets are never overwritten. The
temporary processed tables, demographics file, and generated PDFs are removed
after publication; reports can be reviewed in Box.

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
