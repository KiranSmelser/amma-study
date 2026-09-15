"""Box input retrieval and transactional publication for Amma study runs."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Callable, Protocol, TypeVar

from box_sdk_gen import BoxCCGAuth, BoxClient, CCGConfig
from box_sdk_gen.box.errors import BoxAPIError, BoxSDKError
from box_sdk_gen.managers.folders import (
    CreateFolderParent,
    UpdateFolderByIdParent,
)
from box_sdk_gen.managers.uploads import (
    UploadFileAttributes,
    UploadFileAttributesParentField,
    UploadFileVersionAttributes,
)

from ..box_config import BoxSettings
from ..manifest import sha256_file
from ..reporting import ReportSetPlan


PROCESSED_RUN_FILES = frozenset(
    {
        "participants.csv",
        "participant_days.csv",
        "seizure_events.csv",
        "seizure_event_triggers.csv",
        "seizure_type_dictionary.csv",
        "form_responses.csv",
        "qc_results.csv",
        "data_dictionary.md",
        "run_manifest.json",
    }
)


class BoxStorageError(RuntimeError):
    """A Box workflow failure safe to display at the CLI boundary."""


@dataclass(frozen=True)
class BoxItemRecord:
    item_id: str
    name: str
    item_type: str


@dataclass(frozen=True)
class BoxFileRecord:
    file_id: str
    version_id: str | None
    name: str
    size_bytes: int
    box_sha1: str
    parent_folder_id: str | None
    modified_at: str | None = None

    def manifest_dict(self) -> dict[str, str | int | None]:
        return asdict(self)


@dataclass(frozen=True)
class BoxPublication:
    run_folder_id: str
    relative_path: str
    latest_updated: bool
    output_files: dict[str, BoxFileRecord]


class BoxGateway(Protocol):
    def get_file(self, file_id: str) -> BoxFileRecord: ...

    def download_file(
        self, file_id: str, destination: Path, *, version_id: str | None = None
    ) -> None: ...

    def list_folder(self, folder_id: str) -> list[BoxItemRecord]: ...

    def create_folder(self, parent_id: str, name: str) -> BoxItemRecord: ...

    def move_and_rename_folder(
        self, folder_id: str, parent_id: str, name: str
    ) -> BoxItemRecord: ...

    def delete_folder(self, folder_id: str) -> None: ...

    def upload_file(
        self, parent_id: str, local_path: Path, *, name: str | None = None
    ) -> BoxFileRecord: ...

    def upload_file_version(
        self, file_id: str, local_path: Path, *, name: str
    ) -> BoxFileRecord: ...


T = TypeVar("T")


def _item_type(value: object) -> str:
    enum_value = getattr(value, "value", value)
    return str(enum_value or "")


def _iso_datetime(value: object) -> str | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value) if value not in (None, "") else None


def _sha1_file(path: Path) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class BoxSDKGateway:
    """Small, testable wrapper around the official Box Python SDK."""

    def __init__(self, settings: BoxSettings) -> None:
        auth = BoxCCGAuth(
            CCGConfig(
                client_id=settings.client_id,
                client_secret=settings.client_secret,
                enterprise_id=settings.enterprise_id,
            )
        )
        self.client = BoxClient(auth=auth)

    def _call(self, action: str, operation: Callable[[], T]) -> T:
        try:
            return operation()
        except BoxAPIError as exc:
            response = exc.response_info
            raise BoxStorageError(
                f"Box {action} failed (HTTP {response.status_code}, "
                f"code {response.code or 'unknown'})"
            ) from None
        except BoxSDKError:
            raise BoxStorageError(f"Box {action} failed") from None

    @staticmethod
    def _file_record(item: object) -> BoxFileRecord:
        file_id = getattr(item, "id", None)
        name = getattr(item, "name", None)
        size = getattr(item, "size", None)
        sha1 = getattr(item, "sha_1", None)
        version = getattr(item, "file_version", None)
        parent = getattr(item, "parent", None)
        if not isinstance(file_id, str) or not isinstance(name, str):
            raise BoxStorageError("Box returned incomplete file metadata")
        if not isinstance(size, int) or not isinstance(sha1, str):
            raise BoxStorageError("Box returned no file size or checksum")
        return BoxFileRecord(
            file_id=file_id,
            version_id=getattr(version, "id", None),
            name=name,
            size_bytes=size,
            box_sha1=sha1,
            parent_folder_id=getattr(parent, "id", None),
            modified_at=_iso_datetime(getattr(item, "modified_at", None)),
        )

    def get_file(self, file_id: str) -> BoxFileRecord:
        item = self._call(
            "file metadata request",
            lambda: self.client.files.get_file_by_id(
                file_id,
                fields=[
                    "id",
                    "name",
                    "size",
                    "sha1",
                    "parent",
                    "file_version",
                    "modified_at",
                ],
            ),
        )
        return self._file_record(item)

    def download_file(
        self, file_id: str, destination: Path, *, version_id: str | None = None
    ) -> None:
        stream = self._call(
            "file download",
            lambda: self.client.downloads.download_file(file_id, version=version_id),
        )
        if stream is None:
            raise BoxStorageError("Box file download returned no content")
        try:
            with destination.open("wb") as output:
                shutil.copyfileobj(stream, output)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()

    def list_folder(self, folder_id: str) -> list[BoxItemRecord]:
        offset = 0
        records: list[BoxItemRecord] = []
        while True:
            page = self._call(
                "folder listing",
                lambda: self.client.folders.get_folder_items(
                    folder_id,
                    fields=["id", "name", "type"],
                    limit=1000,
                    offset=offset,
                ),
            )
            entries = page.entries or []
            for item in entries:
                item_id = getattr(item, "id", None)
                name = getattr(item, "name", None)
                if isinstance(item_id, str) and isinstance(name, str):
                    records.append(
                        BoxItemRecord(
                            item_id=item_id,
                            name=name,
                            item_type=_item_type(getattr(item, "type", None)),
                        )
                    )
            total_count = page.total_count
            if not isinstance(total_count, int) or len(records) >= total_count:
                return records
            if not entries:
                raise BoxStorageError("Box folder listing ended before total_count")
            offset += len(entries)

    def create_folder(self, parent_id: str, name: str) -> BoxItemRecord:
        item = self._call(
            "folder creation",
            lambda: self.client.folders.create_folder(
                name, CreateFolderParent(id=parent_id), fields=["id", "name", "type"]
            ),
        )
        return BoxItemRecord(item.id, item.name or name, _item_type(item.type))

    def move_and_rename_folder(
        self, folder_id: str, parent_id: str, name: str
    ) -> BoxItemRecord:
        item = self._call(
            "folder publication",
            lambda: self.client.folders.update_folder_by_id(
                folder_id,
                name=name,
                parent=UpdateFolderByIdParent(id=parent_id),
                fields=["id", "name", "type"],
            ),
        )
        return BoxItemRecord(item.id, item.name or name, _item_type(item.type))

    def delete_folder(self, folder_id: str) -> None:
        self._call(
            "staging cleanup",
            lambda: self.client.folders.delete_folder_by_id(folder_id, recursive=True),
        )

    def upload_file(
        self, parent_id: str, local_path: Path, *, name: str | None = None
    ) -> BoxFileRecord:
        upload_name = name or local_path.name

        def operation() -> object:
            with local_path.open("rb") as handle:
                return self.client.uploads.upload_file(
                    UploadFileAttributes(
                        name=upload_name,
                        parent=UploadFileAttributesParentField(id=parent_id),
                    ),
                    handle,
                    file_file_name=upload_name,
                    fields=["id", "name", "size", "sha1", "parent", "file_version"],
                )

        result = self._call("file upload", operation)
        if not result.entries:
            raise BoxStorageError("Box file upload returned no file metadata")
        return self._file_record(result.entries[0])

    def upload_file_version(
        self, file_id: str, local_path: Path, *, name: str
    ) -> BoxFileRecord:
        def operation() -> object:
            with local_path.open("rb") as handle:
                return self.client.uploads.upload_file_version(
                    file_id,
                    UploadFileVersionAttributes(name=name),
                    handle,
                    file_file_name=name,
                    fields=["id", "name", "size", "sha1", "parent", "file_version"],
                )

        result = self._call("latest pointer update", operation)
        if not result.entries:
            raise BoxStorageError("Box file-version upload returned no metadata")
        return self._file_record(result.entries[0])


class BoxStudyStorage:
    """Study-specific folder boundaries and publication contract."""

    def __init__(self, settings: BoxSettings, gateway: BoxGateway | None = None) -> None:
        self.settings = settings
        self.gateway = gateway or BoxSDKGateway(settings)

    def list_inputs(self) -> dict[str, list[BoxItemRecord]]:
        return {
            "raw": self.gateway.list_folder(self.settings.raw_folder_id),
            "identifiers": self.gateway.list_folder(
                self.settings.identifiers_folder_id
            ),
        }

    def download_input(
        self,
        file_id: str,
        expected_parent_id: str,
        destination: Path,
        *,
        expected_suffix: str,
    ) -> BoxFileRecord:
        if not file_id.isdigit():
            raise BoxStorageError("Box file IDs must contain only digits")
        record = self.gateway.get_file(file_id)
        if record.parent_folder_id != expected_parent_id:
            raise BoxStorageError(
                "Selected Box input is not a direct child of its authorized folder"
            )
        if not record.name.lower().endswith(expected_suffix.lower()):
            raise BoxStorageError(
                f"Selected Box input must have the {expected_suffix} extension"
            )
        self._download_verified_file(record, destination)
        return record

    def _download_verified_file(
        self, record: BoxFileRecord, destination: Path
    ) -> None:
        self.gateway.download_file(
            record.file_id, destination, version_id=record.version_id
        )
        if destination.stat().st_size != record.size_bytes:
            destination.unlink(missing_ok=True)
            raise BoxStorageError("Downloaded Box input size does not match metadata")
        if _sha1_file(destination) != record.box_sha1:
            destination.unlink(missing_ok=True)
            raise BoxStorageError("Downloaded Box input checksum does not match metadata")

    def _one_named_child(
        self, parent_id: str, name: str
    ) -> BoxItemRecord | None:
        matches = [item for item in self.gateway.list_folder(parent_id) if item.name == name]
        if len(matches) > 1:
            raise BoxStorageError(f"Box contains duplicate items named {name!r}")
        return matches[0] if matches else None

    def _ensure_folder(self, parent_id: str, name: str) -> BoxItemRecord:
        existing = self._one_named_child(parent_id, name)
        if existing:
            if existing.item_type != "folder":
                raise BoxStorageError(f"Box item {name!r} exists but is not a folder")
            return existing
        return self.gateway.create_folder(parent_id, name)

    @staticmethod
    def _verify_upload(local_path: Path, uploaded: BoxFileRecord) -> None:
        if uploaded.size_bytes != local_path.stat().st_size:
            raise BoxStorageError("Uploaded Box output size does not match local file")
        if uploaded.box_sha1 != _sha1_file(local_path):
            raise BoxStorageError("Uploaded Box output checksum does not match local file")

    def _upsert_latest(
        self,
        *,
        parent_folder_id: str,
        payload: dict[str, object],
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="amma-box-latest-") as temp_dir:
            latest_path = Path(temp_dir) / "latest.json"
            latest_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            existing = self._one_named_child(parent_folder_id, "latest.json")
            if existing:
                if existing.item_type != "file":
                    raise BoxStorageError(
                        "Box item 'latest.json' exists but is not a file"
                    )
                uploaded = self.gateway.upload_file_version(
                    existing.item_id, latest_path, name="latest.json"
                )
            else:
                uploaded = self.gateway.upload_file(
                    parent_folder_id,
                    latest_path,
                    name="latest.json",
                )
            self._verify_upload(latest_path, uploaded)

    def publish_run(
        self,
        *,
        local_run_directory: Path,
        run_id: str,
        passed: bool,
        source: BoxFileRecord,
        demographics: BoxFileRecord,
    ) -> BoxPublication:
        category = "runs" if passed else "failed"
        category_folder = self._ensure_folder(
            self.settings.processed_folder_id, category
        )
        if self._one_named_child(category_folder.item_id, run_id):
            raise BoxStorageError(f"Box run already exists: {category}/{run_id}")

        staging_name = f"_staging_{run_id}_{uuid.uuid4().hex[:8]}"
        staging = self.gateway.create_folder(
            self.settings.processed_folder_id, staging_name
        )
        staging_exists = True
        manifest_path = local_run_directory / "run_manifest.json"
        original_manifest = manifest_path.read_bytes()
        uploaded_files: dict[str, BoxFileRecord] = {}

        try:
            for path in sorted(local_run_directory.iterdir()):
                if not path.is_file() or path.name == "run_manifest.json":
                    continue
                uploaded = self.gateway.upload_file(staging.item_id, path)
                self._verify_upload(path, uploaded)
                uploaded_files[path.name] = uploaded

            manifest = json.loads(original_manifest)
            manifest["box_publication"] = {
                "processed_parent_folder_id": self.settings.processed_folder_id,
                "run_folder_id": staging.item_id,
                "relative_path": f"{category}/{run_id}",
                "published_utc": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "source": source.manifest_dict(),
                "demographics": demographics.manifest_dict(),
                "output_files": {
                    name: {
                        **record.manifest_dict(),
                        "sha256": sha256_file(local_run_directory / name),
                    }
                    for name, record in sorted(uploaded_files.items())
                },
            }
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            uploaded_manifest = self.gateway.upload_file(
                staging.item_id, manifest_path
            )
            self._verify_upload(manifest_path, uploaded_manifest)
            uploaded_files[manifest_path.name] = uploaded_manifest

            expected_names = {path.name for path in local_run_directory.iterdir() if path.is_file()}
            actual_names = {
                item.name for item in self.gateway.list_folder(staging.item_id)
            }
            if actual_names != expected_names:
                raise BoxStorageError(
                    "Staged Box run contents do not match the local run contents"
                )

            published_folder = self.gateway.move_and_rename_folder(
                staging.item_id, category_folder.item_id, run_id
            )
            staging_exists = False

            if passed:
                self._upsert_latest(
                    parent_folder_id=self.settings.processed_folder_id,
                    payload={
                        "run_id": run_id,
                        "status": "passed",
                        "relative_path": f"runs/{run_id}",
                        "box_run_folder_id": published_folder.item_id,
                        "source_box_file_id": source.file_id,
                        "source_box_version_id": source.version_id,
                        "published_utc": datetime.now(timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z"),
                    },
                )

            return BoxPublication(
                run_folder_id=published_folder.item_id,
                relative_path=f"{category}/{run_id}",
                latest_updated=passed,
                output_files=uploaded_files,
            )
        except Exception:
            if staging_exists:
                try:
                    self.gateway.delete_folder(staging.item_id)
                except Exception as cleanup_error:
                    raise BoxStorageError(
                        "Box publication failed and staging cleanup also failed"
                    ) from cleanup_error
                manifest_path.write_bytes(original_manifest)
            raise

    def _verified_source_run(
        self, source_run_id: str
    ) -> tuple[BoxItemRecord, BoxFileRecord]:
        runs = self._one_named_child(self.settings.processed_folder_id, "runs")
        if not runs or runs.item_type != "folder":
            raise BoxStorageError("Processed/runs is missing in Box")
        source_run = self._one_named_child(runs.item_id, source_run_id)
        if not source_run or source_run.item_type != "folder":
            raise BoxStorageError("Source processed run is missing in Box")
        manifest_item = self._one_named_child(source_run.item_id, "run_manifest.json")
        if not manifest_item or manifest_item.item_type != "file":
            raise BoxStorageError("Source processed run manifest is missing in Box")
        with tempfile.TemporaryDirectory(prefix="amma-source-run-") as temp_dir:
            path = Path(temp_dir) / "run_manifest.json"
            record = self.download_input(
                manifest_item.item_id,
                source_run.item_id,
                path,
                expected_suffix=".json",
            )
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise BoxStorageError("Source processed run manifest is invalid") from exc
        if manifest.get("status") != "passed" or manifest.get("run_id") != source_run_id:
            raise BoxStorageError("Source processed run did not pass provenance checks")
        return source_run, record

    def download_processed_run(
        self, source_run_id: str, destination: Path
    ) -> BoxItemRecord:
        """Download and verify one complete passing processed run from Box."""

        if destination.exists():
            raise BoxStorageError("Processed-run workspace already exists")
        source_run, _ = self._verified_source_run(source_run_id)
        items = self.gateway.list_folder(source_run.item_id)
        if any(item.item_type != "file" for item in items):
            raise BoxStorageError("Processed Box run contains a non-file item")
        item_names = {item.name for item in items}
        if len(item_names) != len(items) or item_names != PROCESSED_RUN_FILES:
            raise BoxStorageError("Processed Box run has unexpected or missing files")

        destination.mkdir(mode=0o700, parents=True)
        records: dict[str, BoxFileRecord] = {}
        for item in items:
            record = self.gateway.get_file(item.item_id)
            if (
                record.parent_folder_id != source_run.item_id
                or record.name != item.name
            ):
                raise BoxStorageError("Processed Box file metadata changed during download")
            self._download_verified_file(record, destination / item.name)
            records[item.name] = record

        manifest_path = destination / "run_manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BoxStorageError("Source processed run manifest is invalid") from exc
        if manifest.get("status") != "passed" or manifest.get("run_id") != source_run_id:
            raise BoxStorageError("Source processed run did not pass provenance checks")

        expected_outputs = manifest.get("output_files")
        if not isinstance(expected_outputs, dict):
            raise BoxStorageError("Source processed run manifest has no output inventory")
        if set(expected_outputs) != PROCESSED_RUN_FILES - {"run_manifest.json"}:
            raise BoxStorageError("Source processed run manifest inventory is incomplete")
        for name, expected in expected_outputs.items():
            if not isinstance(expected, dict):
                raise BoxStorageError("Source processed run manifest inventory is invalid")
            path = destination / name
            if (
                expected.get("size_bytes") != path.stat().st_size
                or expected.get("sha256") != sha256_file(path)
            ):
                raise BoxStorageError("Downloaded processed output failed provenance checks")

        publication = manifest.get("box_publication")
        published_outputs = (
            publication.get("output_files") if isinstance(publication, dict) else None
        )
        if not isinstance(published_outputs, dict):
            raise BoxStorageError("Source processed run has no Box publication inventory")
        if set(published_outputs) != PROCESSED_RUN_FILES - {"run_manifest.json"}:
            raise BoxStorageError("Source Box publication inventory is incomplete")
        for name, published in published_outputs.items():
            record = records[name]
            if not isinstance(published, dict) or (
                published.get("file_id") != record.file_id
                or published.get("version_id") != record.version_id
                or published.get("box_sha1") != record.box_sha1
            ):
                raise BoxStorageError("Processed Box output no longer matches its manifest")
        return source_run

    def publish_report_set(self, plan: ReportSetPlan) -> BoxPublication:
        """Publish a validated report set without exposing names in Box paths."""

        source_run, source_manifest = self._verified_source_run(plan.source_run_id)
        report_runs = self._ensure_folder(self.settings.reports_folder_id, "runs")
        if self._one_named_child(report_runs.item_id, plan.report_set_id):
            raise BoxStorageError(
                f"Box report set already exists: runs/{plan.report_set_id}"
            )

        staging = self.gateway.create_folder(
            self.settings.reports_folder_id,
            f"_staging_{plan.report_set_id}_{uuid.uuid4().hex[:8]}",
        )
        staging_exists = True
        manifest_path = plan.report_directory / "report_manifest.json"
        original_manifest = manifest_path.read_bytes()
        uploaded_files: dict[str, BoxFileRecord] = {}
        participant_folders: dict[str, BoxItemRecord] = {}

        try:
            for row in plan.entries:
                participant_id = row["participant_id"]
                folder = participant_folders.get(participant_id)
                if folder is None:
                    folder = self.gateway.create_folder(
                        staging.item_id, participant_id
                    )
                    participant_folders[participant_id] = folder
                relative_file = row["relative_file"]
                local_path = plan.report_directory / Path(relative_file)
                uploaded = self.gateway.upload_file(folder.item_id, local_path)
                self._verify_upload(local_path, uploaded)
                uploaded_files[relative_file] = uploaded

            index_path = plan.report_directory / "report_index.csv"
            uploaded_index = self.gateway.upload_file(staging.item_id, index_path)
            self._verify_upload(index_path, uploaded_index)
            uploaded_files[index_path.name] = uploaded_index

            manifest = json.loads(original_manifest)
            manifest.update(
                {
                    "report_set_id": plan.report_set_id,
                    "reporting_script_sha256": plan.reporting_script_sha256,
                    "publisher_pipeline_version": plan.publisher_version,
                    "demographics_sha256": plan.demographics_sha256,
                    "demographics_box": plan.demographics_box,
                    "source_box_run_folder_id": source_run.item_id,
                    "source_box_manifest": source_manifest.manifest_dict(),
                    "box_publication": {
                        "reports_parent_folder_id": self.settings.reports_folder_id,
                        "report_set_folder_id": staging.item_id,
                        "relative_path": f"runs/{plan.report_set_id}",
                        "published_utc": datetime.now(timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "output_files": {
                            name: {
                                **record.manifest_dict(),
                                "sha256": sha256_file(plan.report_directory / name),
                            }
                            for name, record in sorted(uploaded_files.items())
                        },
                    },
                }
            )
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            uploaded_manifest = self.gateway.upload_file(
                staging.item_id, manifest_path
            )
            self._verify_upload(manifest_path, uploaded_manifest)
            uploaded_files[manifest_path.name] = uploaded_manifest

            expected_root_names = {
                *participant_folders,
                "report_index.csv",
                "report_manifest.json",
            }
            actual_root_names = {
                item.name for item in self.gateway.list_folder(staging.item_id)
            }
            if actual_root_names != expected_root_names:
                raise BoxStorageError("Staged Box report root is incomplete")
            for participant_id, folder in participant_folders.items():
                expected = {
                    Path(row["relative_file"]).name
                    for row in plan.entries
                    if row["participant_id"] == participant_id
                }
                actual = {
                    item.name for item in self.gateway.list_folder(folder.item_id)
                }
                if actual != expected:
                    raise BoxStorageError(
                        "Staged Box participant report folder is incomplete"
                    )

            published = self.gateway.move_and_rename_folder(
                staging.item_id, report_runs.item_id, plan.report_set_id
            )
            staging_exists = False
            self._upsert_latest(
                parent_folder_id=self.settings.reports_folder_id,
                payload={
                    "report_set_id": plan.report_set_id,
                    "status": "passed",
                    "relative_path": f"runs/{plan.report_set_id}",
                    "box_report_folder_id": published.item_id,
                    "source_run_id": plan.source_run_id,
                    "source_box_run_folder_id": source_run.item_id,
                    "published_utc": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                },
            )
            return BoxPublication(
                run_folder_id=published.item_id,
                relative_path=f"runs/{plan.report_set_id}",
                latest_updated=True,
                output_files=uploaded_files,
            )
        except Exception:
            if staging_exists:
                try:
                    self.gateway.delete_folder(staging.item_id)
                except Exception as cleanup_error:
                    raise BoxStorageError(
                        "Box report publication failed and staging cleanup also failed"
                    ) from cleanup_error
                manifest_path.write_bytes(original_manifest)
            raise
