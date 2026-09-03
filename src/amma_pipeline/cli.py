"""Command-line interface for the Amma processing pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from . import __version__
from .pipeline import run_local_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="amma-pipeline")
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    process = subcommands.add_parser(
        "process", help="Process one complete local Amma workbook snapshot"
    )
    process.add_argument("--input", required=True, type=Path, help="Amma XLSX export")
    process.add_argument(
        "--demographics", required=True, type=Path, help="Restricted demographic CSV"
    )
    process.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/processed"),
        help="Local root for versioned processed runs",
    )
    process.add_argument(
        "--as-of",
        dest="analysis_as_of_date",
        help="Inclusive YYYY-MM-DD cutoff; defaults to latest Diary date",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "process":
        raise AssertionError(f"Unhandled command: {args.command}")

    try:
        outcome = run_local_pipeline(
            workbook_path=args.input,
            demographics_path=args.demographics,
            output_root=args.output_root,
            analysis_as_of_date=args.analysis_as_of_date,
        )
    except Exception as exc:  # CLI boundary: return a concise error without secrets.
        print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
        return 1

    print(
        json.dumps(
            {
                "run_id": outcome.run_id,
                "status": outcome.status,
                "output_directory": str(outcome.output_directory),
                "qc_error_count": outcome.qc_error_count,
                "qc_warning_count": outcome.qc_warning_count,
                "table_row_counts": outcome.table_row_counts,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if outcome.status == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
