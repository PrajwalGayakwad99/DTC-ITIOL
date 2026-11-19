"""Small SQLite persistence layer for timestamped telemetry."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .schemas import TelemetryIn, TelemetryRecord


class TelemetryStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    controller_mode TEXT,
                    wifi_connected INTEGER,
                    wifi_rssi_dbm INTEGER,
                    ip_address TEXT,
                    sensors_json TEXT NOT NULL
                )"""
            )
            existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(telemetry)")}
            for name, definition in (
                ("controller_mode", "TEXT"),
                ("wifi_connected", "INTEGER"),
                ("wifi_rssi_dbm", "INTEGER"),
                ("ip_address", "TEXT"),
            ):
                if name not in existing_columns:
                    connection.execute(f"ALTER TABLE telemetry ADD COLUMN {name} {definition}")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS telemetry_time_idx ON telemetry(timestamp DESC)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def add(self, telemetry: TelemetryIn) -> TelemetryRecord:
        with closing(self._connect()) as connection, connection:
            duplicate = connection.execute(
                "SELECT * FROM telemetry WHERE device_id = ? AND timestamp = ? ORDER BY id DESC LIMIT 1",
                (telemetry.device_id, telemetry.timestamp.isoformat()),
            ).fetchone()
            if duplicate is not None:
                return self._record_from_row(duplicate)
            cursor = connection.execute(
                """INSERT INTO telemetry(
                    device_id, timestamp, mode, controller_mode, wifi_connected,
                    wifi_rssi_dbm, ip_address, sensors_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    telemetry.device_id,
                    telemetry.timestamp.isoformat(),
                    telemetry.mode.value,
                    telemetry.controller_mode.value if telemetry.controller_mode else None,
                    None if telemetry.wifi_connected is None else int(telemetry.wifi_connected),
                    telemetry.wifi_rssi_dbm,
                    telemetry.ip_address,
                    json.dumps([sensor.model_dump(mode="json") for sensor in telemetry.sensors]),
                ),
            )
            record_id = int(cursor.lastrowid)
        return TelemetryRecord(id=record_id, **telemetry.model_dump())

    def latest(self, limit: int = 100, device_id: str | None = None) -> list[TelemetryRecord]:
        query = "SELECT * FROM telemetry"
        params: tuple[object, ...] = ()
        if device_id is not None:
            query += " WHERE device_id = ?"
            params = (device_id,)
        query += " ORDER BY timestamp DESC, id DESC LIMIT ?"
        params += (limit,)
        with closing(self._connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._record_from_row(row) for row in rows]

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> TelemetryRecord:
        return TelemetryRecord(
            id=row["id"],
            device_id=row["device_id"],
            timestamp=row["timestamp"],
            mode=row["mode"],
            controller_mode=row["controller_mode"],
            wifi_connected=None if row["wifi_connected"] is None else bool(row["wifi_connected"]),
            wifi_rssi_dbm=row["wifi_rssi_dbm"],
            ip_address=row["ip_address"],
            sensors=json.loads(row["sensors_json"]),
        )
