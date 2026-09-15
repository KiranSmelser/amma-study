from __future__ import annotations

import pytest

from amma_pipeline.cli import build_parser


def test_cli_exposes_only_box_data_workflows() -> None:
    parser = build_parser()
    help_text = parser.format_help()

    assert "box-inputs" in help_text
    assert "process-box" in help_text
    assert "generate-reports-box" in help_text
    assert "publish-reports-box" not in help_text

    with pytest.raises(SystemExit):
        parser.parse_args(["process"])
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "process-box",
                "--workbook-file-id",
                "101",
                "--demographics-file-id",
                "201",
                "--output-root",
                "data/processed",
            ]
        )
