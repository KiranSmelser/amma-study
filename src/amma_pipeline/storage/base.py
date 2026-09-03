"""Storage protocol shared by local and future Box adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class RunStorage(Protocol):
    """Minimal interface required to stage and publish a processed run."""

    def create_staging_directory(self, run_id: str) -> Path: ...

    def publish(self, staging_directory: Path, run_id: str, *, passed: bool) -> Path: ...
