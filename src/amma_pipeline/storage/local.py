"""Atomic local-filesystem publication for processed runs."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path


class LocalRunStorage:
    """Stage locally, then publish a complete run with one atomic rename."""

    def __init__(self, output_root: str | Path) -> None:
        self.output_root = Path(output_root)

    def create_staging_directory(self, run_id: str) -> Path:
        staging = self.output_root / ".staging" / f"{run_id}-{uuid.uuid4().hex[:8]}"
        staging.mkdir(parents=True, exist_ok=False)
        return staging

    def publish(self, staging_directory: Path, run_id: str, *, passed: bool) -> Path:
        parent = self.output_root / ("runs" if passed else "failed")
        parent.mkdir(parents=True, exist_ok=True)
        destination = parent / run_id
        if destination.exists():
            raise FileExistsError(f"Run already exists: {destination}")
        staging_directory.replace(destination)

        if passed:
            latest = {
                "run_id": run_id,
                "status": "passed",
                "relative_path": str(Path("runs") / run_id),
                "published_utc": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }
            self.output_root.mkdir(parents=True, exist_ok=True)
            temporary = self.output_root / f".latest-{uuid.uuid4().hex}.json"
            temporary.write_text(json.dumps(latest, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.output_root / "latest.json")

        return destination
