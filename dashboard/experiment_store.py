"""SQLite storage for operator experiment notes."""

import os
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path(os.environ.get(
    "DTC_ITOL_EXPERIMENT_DB",
    Path(__file__).resolve().parents[1] / "data" / "experiments.sqlite3",
))
METADATA_PREFIX = "__DTC_ITOL_EXPERIMENT_V1__:"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE IF NOT EXISTS experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )"""
    )
    return connection


def log_experiment(name: str, notes: str) -> int:
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("experiment name is required")
    with connect() as connection:
        cursor = connection.execute(
            "INSERT INTO experiments(name, notes, created_at) VALUES (?, ?, ?)",
            (normalized_name, notes.strip(), datetime.now(timezone.utc).isoformat()),
        )
        return int(cursor.lastrowid)


def log_experiment_details(
    name: str,
    description: str,
    notes: str,
    start_at: datetime,
    end_at: datetime,
) -> int:
    """Persist optional experiment metadata in the existing notes field.

    The existing SQLite table has no description/window columns. This versioned
    envelope keeps that schema unchanged and remains readable alongside legacy notes.
    """
    if start_at.tzinfo is None or end_at.tzinfo is None:
        raise ValueError("experiment start and end timestamps must include a timezone")
    start = start_at.astimezone(timezone.utc)
    end = end_at.astimezone(timezone.utc)
    if end < start:
        raise ValueError("experiment end cannot be before its start")
    metadata = {
        "description": description.strip(),
        "notes": notes.strip(),
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
    }
    encoded = METADATA_PREFIX + json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
    return log_experiment(name, encoded)


def display_experiment(record: dict) -> dict:
    """Decode new metadata while preserving display for existing plain-text notes."""
    result = dict(record)
    raw_notes = str(result.get("notes") or "")
    metadata = None
    if raw_notes.startswith(METADATA_PREFIX):
        try:
            metadata = json.loads(raw_notes[len(METADATA_PREFIX):])
        except (TypeError, ValueError):
            metadata = None
    if isinstance(metadata, dict):
        result.update(metadata)
        result["notes"] = str(metadata.get("notes") or "")
        result["description"] = str(metadata.get("description") or "")
        result["metadata_available"] = True
    else:
        result["description"] = ""
        result["start_at"] = result.get("created_at")
        result["end_at"] = None
        result["notes"] = raw_notes
        result["metadata_available"] = False
    return result


def list_experiments(limit: int = 100) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT id, name, notes, created_at FROM experiments ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 500)),),
        ).fetchall()
    return [dict(row) for row in rows]
