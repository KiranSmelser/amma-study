"""Secure configuration loading for the Amma Box integration."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_KEYCHAIN_SERVICE = "amma-study-box-client-secret"
DEFAULT_KEYCHAIN_ACCOUNT = "amma-study"


class BoxConfigurationError(RuntimeError):
    """A safely printable Box configuration failure."""


@dataclass(frozen=True)
class BoxSettings:
    client_id: str
    enterprise_id: str
    raw_folder_id: str
    identifiers_folder_id: str
    processed_folder_id: str
    documentation_folder_id: str
    reports_folder_id: str
    client_secret: str = field(repr=False)


def read_dotenv(path: str | Path) -> dict[str, str]:
    """Read the restricted, simple KEY=VALUE syntax used by this project."""

    dotenv_path = Path(path)
    if not dotenv_path.is_file():
        raise BoxConfigurationError(f"Box configuration file not found: {dotenv_path}")

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        dotenv_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise BoxConfigurationError(
                f"Invalid Box configuration syntax on line {line_number}"
            )
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _keychain_secret(service: str, account: str) -> str:
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                service,
                "-a",
                account,
                "-w",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BoxConfigurationError(
            "Could not retrieve the Amma Box client secret from macOS Keychain"
        ) from exc
    secret = result.stdout.rstrip("\r\n")
    if not secret:
        raise BoxConfigurationError("The Amma Box client secret in Keychain is empty")
    return secret


def load_box_settings(env_file: str | Path = ".env") -> BoxSettings:
    """Load Box identifiers from env/.env and the secret from env or Keychain."""

    values = read_dotenv(env_file)

    def configured(name: str, *, default: str = "") -> str:
        return os.environ.get(name) or values.get(name) or default

    def required(name: str) -> str:
        value = configured(name)
        if not value:
            raise BoxConfigurationError(
                f"Missing required Box configuration variable: {name}"
            )
        return value

    client_secret = configured("BOX_CLIENT_SECRET")
    if not client_secret:
        client_secret = _keychain_secret(
            configured("BOX_KEYCHAIN_SERVICE", default=DEFAULT_KEYCHAIN_SERVICE),
            configured("BOX_KEYCHAIN_ACCOUNT", default=DEFAULT_KEYCHAIN_ACCOUNT),
        )

    return BoxSettings(
        client_id=required("BOX_CLIENT_ID"),
        enterprise_id=required("BOX_ENTERPRISE_ID"),
        raw_folder_id=required("BOX_AMMA_RAW_FOLDER_ID"),
        identifiers_folder_id=required("BOX_AMMA_IDENTIFIERS_FOLDER_ID"),
        processed_folder_id=required("BOX_AMMA_PROCESSED_FOLDER_ID"),
        documentation_folder_id=required("BOX_AMMA_DOCUMENTATION_FOLDER_ID"),
        reports_folder_id=required("BOX_AMMA_REPORTS_FOLDER_ID"),
        client_secret=client_secret,
    )
