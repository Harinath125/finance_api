"""
Just reads the CSV off disk into raw dicts - no normalizing or validating,
that's validator.py's job. Keeping this dumb means the validator can be
tested with plain dicts, no filesystem needed.
"""

from __future__ import annotations

import csv
from pathlib import Path


class DataLoadError(Exception):
    """Raised when the dataset file cannot be read at all (missing, unreadable)."""


def load_opportunities_raw(path: str | Path) -> list[dict]:
    """
    Read the opportunities CSV into a list of raw row dicts (values stay
    as strings, exactly as they appear in the file).
    """
    path = Path(path)
    if not path.exists():
        raise DataLoadError(f"Dataset file not found: {path}")

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f) # becuase it binds the each field value with the header of the field.
            rows = [dict(row) for row in reader]
    except (OSError, csv.Error) as exc:
        raise DataLoadError(f"Could not read dataset file {path}: {exc}") from exc

    if not rows:
        raise DataLoadError(f"Dataset file {path} contains no records")

    return rows
