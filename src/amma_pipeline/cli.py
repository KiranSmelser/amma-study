"""Command-line interface for the Amma processing pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from . import __version__
from .box_config import load_box_settings
from .pipeline import run_box_pipeline, run_box_report_pipeline
from .storage import BoxStudyStorage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="amma-pipeline")
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    box_inputs = subcommands.add_parser(
        "box-inputs", help="List explicit candidate inputs in the authorized Box folders"
    )
    box_inputs.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Local Box configuration file",
    )

    process_box = subcommands.add_parser(
        "process-box",
        help="Process explicit Box input versions and publish a versioned Box run",
    )
    process_box.add_argument(
        "--workbook-file-id", required=True, help="Box file ID in Raw App Exports"
    )
    process_box.add_argument(
        "--demographics-file-id", required=True, help="Box file ID in Identifiers"
    )
    process_box.add_argument(
        "--as-of",
        dest="analysis_as_of_date",
        help="Inclusive YYYY-MM-DD cutoff; defaults to latest Diary date",
    )
    process_box.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Local Box configuration file",
    )

    generate_reports = subcommands.add_parser(
        "generate-reports-box",
        help="Generate and publish reports from an explicit processed Box run",
    )
    generate_reports.add_argument(
        "--source-run-id",
        required=True,
        help="Exact passing processed run used to generate the reports",
    )
    generate_reports.add_argument(
        "--demographics-file-id",
        required=True,
        help="Box file ID in Identifiers used for report names",
    )
    generate_reports.add_argument(
        "--reporting-script",
        type=Path,
        default=Path("scripts/monthly_patient_graphics.R"),
        help="Reporting script included in the deterministic report-set fingerprint",
    )
    generate_reports.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Local Box configuration file",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "box-inputs":
            settings = load_box_settings(args.env_file)
            inputs = BoxStudyStorage(settings).list_inputs()
            result = {
                "status": "passed",
                "folders": {
                    label: [
                        {
                            "file_id": item.item_id,
                            "name": item.name,
                            "type": item.item_type,
                        }
                        for item in items
                    ]
                    for label, items in inputs.items()
                },
            }
            exit_code = 0
        elif args.command == "process-box":
            settings = load_box_settings(args.env_file)
            outcome = run_box_pipeline(
                workbook_file_id=args.workbook_file_id,
                demographics_file_id=args.demographics_file_id,
                settings=settings,
                analysis_as_of_date=args.analysis_as_of_date,
            )
            result = {
                "run_id": outcome.run_id,
                "status": outcome.status,
                "qc_error_count": outcome.qc_error_count,
                "qc_warning_count": outcome.qc_warning_count,
                "table_row_counts": outcome.table_row_counts,
                "box_run_folder_id": outcome.publication.run_folder_id,
                "box_relative_path": outcome.publication.relative_path,
                "box_latest_updated": outcome.publication.latest_updated,
            }
            exit_code = 0 if outcome.status == "passed" else 2
        elif args.command == "generate-reports-box":
            settings = load_box_settings(args.env_file)
            outcome = run_box_report_pipeline(
                source_run_id=args.source_run_id,
                demographics_file_id=args.demographics_file_id,
                settings=settings,
                reporting_script=args.reporting_script,
            )
            result = {
                "status": "passed",
                "report_set_id": outcome.plan.report_set_id,
                "source_run_id": outcome.plan.source_run_id,
                "report_count": len(outcome.plan.entries),
                "box_report_folder_id": outcome.publication.run_folder_id,
                "box_relative_path": outcome.publication.relative_path,
                "box_latest_updated": outcome.publication.latest_updated,
            }
            exit_code = 0
        else:
            raise AssertionError(f"Unhandled command: {args.command}")
    except Exception as exc:  # CLI boundary: return a concise error without secrets.
        print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
        return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
