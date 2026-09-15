from __future__ import annotations

import hashlib
import csv
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from amma_pipeline.box_config import BoxSettings
from amma_pipeline.pipeline import run_box_pipeline, run_box_report_pipeline
from amma_pipeline.reporting import validate_report_set
from amma_pipeline.storage import (
    BoxFileRecord,
    BoxItemRecord,
    BoxStorageError,
    BoxStudyStorage,
)


def _sha1(content: bytes) -> str:
    return hashlib.sha1(content, usedforsecurity=False).hexdigest()


@dataclass
class _FakeItem:
    item_id: str
    parent_id: str
    name: str
    item_type: str
    content: bytes = b""
    version: int = 1


class FakeBoxGateway:
    def __init__(self, settings: BoxSettings) -> None:
        self.items: dict[str, _FakeItem] = {
            settings.raw_folder_id: _FakeItem(
                settings.raw_folder_id, "0", "Raw App Exports", "folder"
            ),
            settings.identifiers_folder_id: _FakeItem(
                settings.identifiers_folder_id, "0", "Identifiers", "folder"
            ),
            settings.processed_folder_id: _FakeItem(
                settings.processed_folder_id, "0", "Processed", "folder"
            ),
            settings.documentation_folder_id: _FakeItem(
                settings.documentation_folder_id, "0", "Documentation", "folder"
            ),
            settings.reports_folder_id: _FakeItem(
                settings.reports_folder_id, "0", "Patient Reports", "folder"
            ),
        }
        self.next_id = 1000

    def add_file(
        self, file_id: str, parent_id: str, name: str, content: bytes
    ) -> None:
        self.items[file_id] = _FakeItem(file_id, parent_id, name, "file", content)

    def _record(self, item: _FakeItem) -> BoxFileRecord:
        return BoxFileRecord(
            file_id=item.item_id,
            version_id=f"v{item.version}",
            name=item.name,
            size_bytes=len(item.content),
            box_sha1=_sha1(item.content),
            parent_folder_id=item.parent_id,
            modified_at="2026-09-15T00:00:00Z",
        )

    def _new_id(self) -> str:
        self.next_id += 1
        return str(self.next_id)

    def get_file(self, file_id: str) -> BoxFileRecord:
        return self._record(self.items[file_id])

    def download_file(
        self, file_id: str, destination: Path, *, version_id: str | None = None
    ) -> None:
        destination.write_bytes(self.items[file_id].content)

    def list_folder(self, folder_id: str) -> list[BoxItemRecord]:
        return [
            BoxItemRecord(item.item_id, item.name, item.item_type)
            for item in self.items.values()
            if item.parent_id == folder_id
        ]

    def create_folder(self, parent_id: str, name: str) -> BoxItemRecord:
        item = _FakeItem(self._new_id(), parent_id, name, "folder")
        self.items[item.item_id] = item
        return BoxItemRecord(item.item_id, item.name, item.item_type)

    def move_and_rename_folder(
        self, folder_id: str, parent_id: str, name: str
    ) -> BoxItemRecord:
        item = self.items[folder_id]
        item.parent_id = parent_id
        item.name = name
        return BoxItemRecord(item.item_id, item.name, item.item_type)

    def delete_folder(self, folder_id: str) -> None:
        children = [
            item.item_id for item in self.items.values() if item.parent_id == folder_id
        ]
        for child in children:
            if self.items[child].item_type == "folder":
                self.delete_folder(child)
            else:
                del self.items[child]
        del self.items[folder_id]

    def upload_file(
        self, parent_id: str, local_path: Path, *, name: str | None = None
    ) -> BoxFileRecord:
        item = _FakeItem(
            self._new_id(),
            parent_id,
            name or local_path.name,
            "file",
            local_path.read_bytes(),
        )
        self.items[item.item_id] = item
        return self._record(item)

    def upload_file_version(
        self, file_id: str, local_path: Path, *, name: str
    ) -> BoxFileRecord:
        item = self.items[file_id]
        item.content = local_path.read_bytes()
        item.name = name
        item.version += 1
        return self._record(item)

    def child(self, parent_id: str, name: str) -> _FakeItem:
        return next(
            item
            for item in self.items.values()
            if item.parent_id == parent_id and item.name == name
        )


@pytest.fixture
def box_settings() -> BoxSettings:
    return BoxSettings(
        client_id="client",
        enterprise_id="enterprise",
        raw_folder_id="10",
        identifiers_folder_id="20",
        processed_folder_id="30",
        documentation_folder_id="40",
        reports_folder_id="50",
        client_secret="secret",
    )


def test_box_pipeline_uses_explicit_inputs_and_publishes_verified_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_inputs,
    box_settings: BoxSettings,
) -> None:
    workbook, demographics = synthetic_inputs()
    gateway = FakeBoxGateway(box_settings)
    gateway.add_file("101", "10", "amma-export.xlsx", workbook.read_bytes())
    gateway.add_file("201", "20", "demo.csv", demographics.read_bytes())
    temporary_parent = tmp_path / "private-temporary-workspaces"
    temporary_parent.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporary_parent))

    outcome = run_box_pipeline(
        workbook_file_id="101",
        demographics_file_id="201",
        settings=box_settings,
        gateway=gateway,
    )

    assert outcome.status == "passed"
    assert outcome.publication.relative_path == f"runs/{outcome.run_id}"
    runs = gateway.child("30", "runs")
    run_folder = gateway.child(runs.item_id, outcome.run_id)
    latest = json.loads(gateway.child("30", "latest.json").content)
    assert latest["run_id"] == outcome.run_id
    assert latest["box_run_folder_id"] == run_folder.item_id
    assert latest["source_box_file_id"] == "101"

    manifest_item = gateway.child(run_folder.item_id, "run_manifest.json")
    manifest = json.loads(manifest_item.content)
    assert manifest["source_box_file_id"] == "101"
    assert manifest["source_box_version_id"] == "v1"
    assert manifest["source_filename"] == "amma-export.xlsx"
    assert manifest["demographics_box_file_id"] == "201"
    assert manifest["demographics_box_version_id"] == "v1"
    assert manifest["demographics_filename"] == "demo.csv"
    publication = manifest["box_publication"]
    assert publication["run_folder_id"] == run_folder.item_id
    assert publication["relative_path"] == f"runs/{outcome.run_id}"
    assert publication["source"]["file_id"] == "101"
    assert publication["demographics"]["file_id"] == "201"
    assert len(publication["output_files"]["participants.csv"]["sha256"]) == 64
    assert not any(item.name.startswith("_staging_") for item in gateway.items.values())
    assert not list(temporary_parent.iterdir())


def test_failed_box_run_is_published_without_updating_latest(
    tmp_path: Path, synthetic_inputs, box_settings: BoxSettings
) -> None:
    workbook, demographics = synthetic_inputs(seizure_diary_count=2)
    gateway = FakeBoxGateway(box_settings)
    gateway.add_file("101", "10", "amma-export.xlsx", workbook.read_bytes())
    gateway.add_file("201", "20", "demo.csv", demographics.read_bytes())

    outcome = run_box_pipeline(
        workbook_file_id="101",
        demographics_file_id="201",
        settings=box_settings,
        gateway=gateway,
    )

    assert outcome.status == "failed"
    assert outcome.publication.relative_path == f"failed/{outcome.run_id}"
    failed = gateway.child("30", "failed")
    gateway.child(failed.item_id, outcome.run_id)
    assert not any(
        item.parent_id == "30" and item.name == "latest.json"
        for item in gateway.items.values()
    )


def test_box_pipeline_rejects_input_outside_expected_folder(
    tmp_path: Path, synthetic_inputs, box_settings: BoxSettings
) -> None:
    workbook, demographics = synthetic_inputs()
    gateway = FakeBoxGateway(box_settings)
    gateway.add_file("101", "20", "amma-export.xlsx", workbook.read_bytes())
    gateway.add_file("201", "20", "demo.csv", demographics.read_bytes())

    with pytest.raises(BoxStorageError, match="not a direct child"):
        run_box_pipeline(
            workbook_file_id="101",
            demographics_file_id="201",
            settings=box_settings,
            gateway=gateway,
        )


def test_patient_reports_publish_with_id_only_paths_and_source_provenance(
    tmp_path: Path, box_settings: BoxSettings
) -> None:
    source_run_id = "run_20260915T193737Z_0adefd24"
    gateway = FakeBoxGateway(box_settings)
    processed_runs = gateway.create_folder("30", "runs")
    source_run = gateway.create_folder(processed_runs.item_id, source_run_id)
    source_manifest = tmp_path / "source_manifest.json"
    source_manifest.write_text(
        json.dumps({"run_id": source_run_id, "status": "passed"}),
        encoding="utf-8",
    )
    gateway.add_file(
        "301",
        source_run.item_id,
        "run_manifest.json",
        source_manifest.read_bytes(),
    )

    report_directory = tmp_path / "reports" / source_run_id
    participant_directory = report_directory / "SCN8A-010"
    participant_directory.mkdir(parents=True)
    pdf_path = participant_directory / "SCN8A-010_2026-09.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nsynthetic report\n")
    with (report_directory / "report_index.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "participant_id",
                "month",
                "source_run",
                "relative_file",
            ),
        )
        writer.writeheader()
        writer.writerow(
            {
                "participant_id": "SCN8A-010",
                "month": "2026-09",
                "source_run": source_run_id,
                "relative_file": "SCN8A-010/SCN8A-010_2026-09.pdf",
            }
        )
    (report_directory / "report_manifest.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "report_schema_version": "1.0.0",
                "source_run_id": source_run_id,
                "report_count": 1,
            }
        ),
        encoding="utf-8",
    )
    reporting_script = tmp_path / "monthly_patient_graphics.R"
    reporting_script.write_text("# synthetic reporting script\n", encoding="utf-8")

    plan = validate_report_set(
        report_directory,
        source_run_id=source_run_id,
        reporting_script=reporting_script,
        demographics_box={
            "file_id": "201",
            "version_id": "v1",
            "box_sha1": "a" * 40,
        },
        demographics_sha256="b" * 64,
    )
    publication = BoxStudyStorage(box_settings, gateway).publish_report_set(plan)

    report_runs = gateway.child("50", "runs")
    report_set = gateway.child(report_runs.item_id, plan.report_set_id)
    participant = gateway.child(report_set.item_id, "SCN8A-010")
    gateway.child(participant.item_id, "SCN8A-010_2026-09.pdf")
    index = gateway.child(report_set.item_id, "report_index.csv").content.decode()
    assert "patient_name" not in index
    assert "SCN8A-010/SCN8A-010_2026-09.pdf" in index

    report_manifest = json.loads(
        gateway.child(report_set.item_id, "report_manifest.json").content
    )
    assert report_manifest["source_box_run_folder_id"] == source_run.item_id
    assert report_manifest["report_set_id"] == plan.report_set_id
    assert report_manifest["reporting_script_sha256"] == plan.reporting_script_sha256
    assert report_manifest["publisher_pipeline_version"] == "0.4.0"
    assert report_manifest["demographics_box"]["file_id"] == "201"
    assert report_manifest["demographics_sha256"] == "b" * 64
    output = report_manifest["box_publication"]["output_files"]
    assert len(output["SCN8A-010/SCN8A-010_2026-09.pdf"]["sha256"]) == 64

    latest = json.loads(gateway.child("50", "latest.json").content)
    assert latest["report_set_id"] == plan.report_set_id
    assert latest["source_run_id"] == source_run_id
    assert publication.run_folder_id == report_set.item_id


def test_box_report_pipeline_downloads_generates_publishes_and_cleans_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_inputs,
    box_settings: BoxSettings,
) -> None:
    workbook, demographics_path = synthetic_inputs()
    gateway = FakeBoxGateway(box_settings)
    gateway.add_file("101", "10", "amma-export.xlsx", workbook.read_bytes())
    gateway.add_file("201", "20", "demo.csv", demographics_path.read_bytes())

    processed = run_box_pipeline(
        workbook_file_id="101",
        demographics_file_id="201",
        settings=box_settings,
        gateway=gateway,
    )
    reporting_script = tmp_path / "monthly_patient_graphics.R"
    reporting_script.write_text("# synthetic reporting script\n", encoding="utf-8")
    temporary_parent = tmp_path / "private-temporary-workspaces"
    temporary_parent.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporary_parent))

    def fake_report_runner(
        processed_run_directory: Path,
        reports_root: Path,
        downloaded_demographics: Path,
        script_path: Path,
    ) -> None:
        assert (processed_run_directory / "participant_days.csv").is_file()
        assert downloaded_demographics.read_bytes() == demographics_path.read_bytes()
        assert script_path == reporting_script.resolve()
        report_directory = reports_root / processed.run_id
        participant_directory = report_directory / "SCN8A-010"
        participant_directory.mkdir(parents=True)
        relative_file = "SCN8A-010/SCN8A-010_2026-09.pdf"
        (report_directory / relative_file).write_bytes(b"%PDF-1.4\nsynthetic report\n")
        with (report_directory / "report_index.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=(
                    "participant_id",
                    "month",
                    "source_run",
                    "relative_file",
                ),
            )
            writer.writeheader()
            writer.writerow(
                {
                    "participant_id": "SCN8A-010",
                    "month": "2026-09",
                    "source_run": processed.run_id,
                    "relative_file": relative_file,
                }
            )
        (report_directory / "report_manifest.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "report_schema_version": "1.0.0",
                    "source_run_id": processed.run_id,
                    "report_count": 1,
                }
            ),
            encoding="utf-8",
        )

    outcome = run_box_report_pipeline(
        source_run_id=processed.run_id,
        demographics_file_id="201",
        settings=box_settings,
        reporting_script=reporting_script,
        gateway=gateway,
        report_runner=fake_report_runner,
    )

    assert outcome.plan.source_run_id == processed.run_id
    report_runs = gateway.child("50", "runs")
    report_set = gateway.child(report_runs.item_id, outcome.plan.report_set_id)
    manifest = json.loads(
        gateway.child(report_set.item_id, "report_manifest.json").content
    )
    assert manifest["demographics_box"]["file_id"] == "201"
    assert manifest["demographics_box"]["version_id"] == "v1"
    assert len(manifest["demographics_sha256"]) == 64
    assert not list(temporary_parent.iterdir())
