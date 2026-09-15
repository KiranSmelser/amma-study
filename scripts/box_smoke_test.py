#!/usr/bin/env python3
"""Permission and round-trip smoke test for the Amma Box service account.

The script intentionally avoids logging credentials, access tokens, item names,
or downloaded file contents. All synthetic uploads are deleted, including when
a nominally read-only folder is accidentally writable.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


API_BASE = "https://api.box.com/2.0"
UPLOAD_BASE = "https://upload.box.com/api/2.0"
TOKEN_URL = "https://api.box.com/oauth2/token"
KEYCHAIN_SERVICE = "amma-study-box-client-secret"
KEYCHAIN_ACCOUNT = "amma-study"


def ssl_context() -> ssl.SSLContext:
    """Use Python's CA bundle, falling back to the macOS system bundle."""

    default_cafile = ssl.get_default_verify_paths().cafile
    if default_cafile:
        return ssl.create_default_context(cafile=default_cafile)
    macos_cafile = Path("/etc/ssl/cert.pem")
    if macos_cafile.is_file():
        return ssl.create_default_context(cafile=str(macos_cafile))
    return ssl.create_default_context()


SSL_CONTEXT = ssl_context()


class SmokeTestError(RuntimeError):
    """A safely printable smoke-test failure."""


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    enterprise_id: str
    raw_folder_id: str
    identifiers_folder_id: str
    processed_folder_id: str
    documentation_folder_id: str
    reports_folder_id: str

    @property
    def folders(self) -> dict[str, str]:
        return {
            "raw": self.raw_folder_id,
            "identifiers": self.identifiers_folder_id,
            "processed": self.processed_folder_id,
            "documentation": self.documentation_folder_id,
            "reports": self.reports_folder_id,
        }


def read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SmokeTestError(f"Invalid .env syntax on line {line_number}")
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def keychain_secret() -> str:
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                KEYCHAIN_ACCOUNT,
                "-w",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SmokeTestError(
            "Could not retrieve the Amma Box client secret from macOS Keychain"
        ) from exc
    secret = result.stdout.rstrip("\r\n")
    if not secret:
        raise SmokeTestError("The Amma Box client secret in Keychain is empty")
    return secret


def load_settings(project_root: Path) -> Settings:
    dotenv_path = project_root / ".env"
    if not dotenv_path.is_file():
        raise SmokeTestError("Missing local .env file")
    values = read_dotenv(dotenv_path)

    def required(name: str) -> str:
        value = os.environ.get(name) or values.get(name, "")
        if not value:
            raise SmokeTestError(f"Missing required configuration variable: {name}")
        return value

    client_secret = os.environ.get("BOX_CLIENT_SECRET") or values.get(
        "BOX_CLIENT_SECRET", ""
    )
    if not client_secret:
        client_secret = keychain_secret()

    return Settings(
        client_id=required("BOX_CLIENT_ID"),
        client_secret=client_secret,
        enterprise_id=required("BOX_ENTERPRISE_ID"),
        raw_folder_id=required("BOX_AMMA_RAW_FOLDER_ID"),
        identifiers_folder_id=required("BOX_AMMA_IDENTIFIERS_FOLDER_ID"),
        processed_folder_id=required("BOX_AMMA_PROCESSED_FOLDER_ID"),
        documentation_folder_id=required("BOX_AMMA_DOCUMENTATION_FOLDER_ID"),
        reports_folder_id=required("BOX_AMMA_REPORTS_FOLDER_ID"),
    )


def request(
    url: str,
    *,
    method: str = "GET",
    token: str | None = None,
    data: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, bytes]:
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=45, context=SSL_CONTEXT) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        raise BoxHttpError(exc.code, body) from None
    except urllib.error.URLError as exc:
        raise SmokeTestError("Could not connect to Box") from exc


class BoxHttpError(Exception):
    def __init__(self, status: int, body: bytes) -> None:
        super().__init__(status)
        self.status = status
        self.code = "unknown"
        try:
            parsed = json.loads(body)
            code = parsed.get("code") or parsed.get("error")
            if isinstance(code, str):
                self.code = code
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass


def json_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
    _, body = request(*args, **kwargs)
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SmokeTestError("Box returned a non-JSON response") from exc
    if not isinstance(parsed, dict):
        raise SmokeTestError("Box returned an unexpected JSON response")
    return parsed


def obtain_token(settings: Settings) -> str:
    payload = urllib.parse.urlencode(
        {
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "grant_type": "client_credentials",
            "box_subject_type": "enterprise",
            "box_subject_id": settings.enterprise_id,
        }
    ).encode("utf-8")
    try:
        response = json_request(
            TOKEN_URL,
            method="POST",
            data=payload,
            content_type="application/x-www-form-urlencoded",
        )
    except BoxHttpError as exc:
        raise SmokeTestError(
            f"Box authentication failed (HTTP {exc.status}, code {exc.code})"
        ) from None
    token = response.get("access_token")
    if not isinstance(token, str) or not token:
        raise SmokeTestError("Box authentication returned no access token")
    return token


def folder_entries(token: str, folder_id: str) -> list[dict[str, Any]]:
    offset = 0
    entries: list[dict[str, Any]] = []
    while True:
        query = urllib.parse.urlencode(
            {"fields": "id,type,size", "limit": 1000, "offset": offset}
        )
        page = json_request(
            f"{API_BASE}/folders/{folder_id}/items?{query}", token=token
        )
        page_entries = page.get("entries")
        if not isinstance(page_entries, list):
            raise SmokeTestError("Box folder listing had an unexpected structure")
        entries.extend(item for item in page_entries if isinstance(item, dict))
        total_count = page.get("total_count")
        if not isinstance(total_count, int) or len(entries) >= total_count:
            return entries
        offset = len(entries)


def multipart_upload(token: str, folder_id: str, filename: str, content: bytes) -> str:
    boundary = f"amma-{secrets.token_hex(16)}"
    attributes = json.dumps({"name": filename, "parent": {"id": folder_id}})
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="attributes"\r\n'
        "Content-Type: application/json\r\n\r\n"
        f"{attributes}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    result = json_request(
        f"{UPLOAD_BASE}/files/content",
        method="POST",
        token=token,
        data=body,
        content_type=f"multipart/form-data; boundary={boundary}",
    )
    entries = result.get("entries")
    if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
        raise SmokeTestError("Box upload returned an unexpected response")
    file_id = entries[0].get("id")
    if not isinstance(file_id, str) or not file_id:
        raise SmokeTestError("Box upload returned no file ID")
    return file_id


def delete_file(token: str, file_id: str) -> None:
    request(f"{API_BASE}/files/{file_id}", method="DELETE", token=token)


def delete_synthetic_file(token: str, file_id: str) -> None:
    """Delete and immediately purge a file created by this smoke-test run."""

    delete_file(token, file_id)
    request(f"{API_BASE}/files/{file_id}/trash", method="DELETE", token=token)


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    checks: list[dict[str, Any]] = []
    created_file_ids: set[str] = set()
    token: str | None = None

    def record(check: str, status: str, detail: str | int | None = None) -> None:
        result: dict[str, Any] = {"check": check, "status": status}
        if detail is not None:
            result["detail"] = detail
        checks.append(result)

    try:
        settings = load_settings(project_root)
        token = obtain_token(settings)

        me = json_request(f"{API_BASE}/users/me?fields=id", token=token)
        if not isinstance(me.get("id"), str):
            raise SmokeTestError("Box did not return a service-account identity")
        record("authenticate_as_service_account", "passed")

        listings: dict[str, list[dict[str, Any]]] = {}
        for label, folder_id in settings.folders.items():
            json_request(
                f"{API_BASE}/folders/{folder_id}?fields=id,item_status", token=token
            )
            listings[label] = folder_entries(token, folder_id)
            record(f"read_{label}_folder", "passed", len(listings[label]))

        raw_files = [item for item in listings["raw"] if item.get("type") == "file"]
        if raw_files and isinstance(raw_files[0].get("id"), str):
            _, downloaded = request(
                f"{API_BASE}/files/{raw_files[0]['id']}/content", token=token
            )
            expected_size = raw_files[0].get("size")
            if isinstance(expected_size, int) and len(downloaded) != expected_size:
                raise SmokeTestError("Downloaded raw test file size did not match metadata")
            hashlib.sha256(downloaded).digest()
            with tempfile.NamedTemporaryFile(prefix="amma-box-", delete=True) as handle:
                handle.write(downloaded)
                handle.flush()
            record("download_raw_file", "passed", len(downloaded))
        else:
            record("download_raw_file", "skipped", "no file available")

        synthetic = b"Synthetic Amma Box permission smoke test. No participant data.\n"
        test_name = f"amma-box-smoke-{secrets.token_hex(12)}.txt"
        for label in ("processed", "reports"):
            uploaded_file_id = multipart_upload(
                token, settings.folders[label], test_name, synthetic
            )
            created_file_ids.add(uploaded_file_id)
            _, round_trip = request(
                f"{API_BASE}/files/{uploaded_file_id}/content", token=token
            )
            if hashlib.sha256(round_trip).digest() != hashlib.sha256(
                synthetic
            ).digest():
                raise SmokeTestError(
                    f"{label.title()}-folder round-trip checksum did not match"
                )
            delete_synthetic_file(token, uploaded_file_id)
            created_file_ids.remove(uploaded_file_id)
            record(f"{label}_upload_download_checksum_delete", "passed")

        for label in ("raw", "identifiers", "documentation"):
            unexpected_file_id: str | None = None
            try:
                unexpected_file_id = multipart_upload(
                    token, settings.folders[label], test_name, synthetic
                )
                created_file_ids.add(unexpected_file_id)
            except BoxHttpError as exc:
                if exc.status in {401, 403, 404}:
                    record(f"deny_write_{label}_folder", "passed")
                    continue
                raise SmokeTestError(
                    f"Unexpected Box response testing {label} write denial "
                    f"(HTTP {exc.status}, code {exc.code})"
                ) from None

            if unexpected_file_id:
                delete_synthetic_file(token, unexpected_file_id)
                created_file_ids.remove(unexpected_file_id)
            record(f"deny_write_{label}_folder", "failed", "folder is writable")

        root_entries = folder_entries(token, "0")
        expected_ids = set(settings.folders.values())
        unexpected_count = sum(
            1 for item in root_entries if str(item.get("id", "")) not in expected_ids
        )
        missing_count = len(
            expected_ids - {str(item.get("id", "")) for item in root_entries}
        )
        if unexpected_count == 0 and missing_count == 0:
            record("service_account_root_isolation", "passed")
        else:
            record(
                "service_account_root_isolation",
                "failed",
                f"{unexpected_count} unexpected, {missing_count} missing",
            )

    except BoxHttpError as exc:
        record("smoke_test", "failed", f"HTTP {exc.status}, code {exc.code}")
    except SmokeTestError as exc:
        record("smoke_test", "failed", str(exc))
    except Exception as exc:  # Keep unexpected failures free of sensitive payloads.
        record("smoke_test", "failed", f"unexpected {type(exc).__name__}")
    finally:
        if token:
            for file_id in list(created_file_ids):
                try:
                    delete_synthetic_file(token, file_id)
                    created_file_ids.remove(file_id)
                except Exception:
                    pass
        if created_file_ids:
            record("synthetic_cleanup", "failed", len(created_file_ids))
        else:
            record("synthetic_cleanup", "passed")

    passed = all(item["status"] in {"passed", "skipped"} for item in checks)
    result = {"status": "passed" if passed else "failed", "checks": checks}
    print(json.dumps(result, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
