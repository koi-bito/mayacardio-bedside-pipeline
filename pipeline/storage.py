import contextlib
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple


class Storage:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.status_file = self.db_path + ".status"
        self.write_errors: int = 0
        self._init_db()

    def record_write_error(self, error_msg: str) -> None:
        """Increment write error count and update status file safely."""
        self.write_errors += 1
        try:
            payload = json.dumps({"write_errors": self.write_errors, "last_error": error_msg})
            with open(self.status_file, "w", encoding="utf-8") as f:
                f.write(payload)
        except Exception:
            pass

    def get_write_error_count(self) -> int:
        """Retrieve total write errors recorded from status file or memory."""
        try:
            if os.path.exists(self.status_file):
                with open(self.status_file, "r", encoding="utf-8") as f:
                    data = json.loads(f.read())
                    return int(data.get("write_errors", self.write_errors))
        except Exception:
            pass
        return self.write_errors

    @contextlib.contextmanager
    def connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        # Crash safety settings
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA wal_autocheckpoint = 1000;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        try:
            if not os.path.exists(self.status_file):
                with open(self.status_file, "w", encoding="utf-8") as f:
                    f.write(json.dumps({"write_errors": 0}))
        except Exception:
            pass
        try:
            with self.connection() as conn:
                conn.executescript("""
                CREATE TABLE IF NOT EXISTS samples (
                    session_id TEXT NOT NULL,
                    sensor INTEGER NOT NULL,
                    sample_index INTEGER NOT NULL,
                    values_json TEXT NOT NULL,
                    PRIMARY KEY (session_id, sensor, sample_index)
                );

                CREATE TABLE IF NOT EXISTS losses (
                    session_id TEXT NOT NULL,
                    sensor INTEGER NOT NULL,
                    from_index INTEGER NOT NULL,
                    to_index INTEGER,
                    reason TEXT NOT NULL,
                    PRIMARY KEY (session_id, sensor, from_index, reason)
                );

                CREATE TABLE IF NOT EXISTS sync_state (
                    session_id TEXT NOT NULL,
                    sensor INTEGER NOT NULL,
                    last_synced_index INTEGER NOT NULL DEFAULT -1,
                    PRIMARY KEY (session_id, sensor)
                );

                CREATE TABLE IF NOT EXISTS results (
                    session_id TEXT NOT NULL,
                    window INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY (session_id, window, mode)
                );

                CREATE INDEX IF NOT EXISTS idx_losses_session 
                    ON losses (session_id, sensor, from_index);
                CREATE INDEX IF NOT EXISTS idx_results_session 
                    ON results (session_id, window);
                """)
        except (sqlite3.Error, OSError) as e:
            self.record_write_error(f"init_db failed: {e}")

    def insert_samples_batch(
        self, session_id: str, sensor: int, first_index: int, data: List[List[int]]
    ) -> int:
        """Insert batch of samples with INSERT OR IGNORE for idempotency."""
        if not data:
            return 0
        rows = [
            (session_id, sensor, first_index + idx, json.dumps(values, separators=(",", ":")))
            for idx, values in enumerate(data)
        ]
        with self.connection() as conn:
            cursor = conn.executemany(
                """
                INSERT OR IGNORE INTO samples (session_id, sensor, sample_index, values_json)
                VALUES (?, ?, ?, ?)
                """,
                rows,
            )
            return cursor.rowcount

    def insert_loss(
        self, session_id: str, sensor: int, from_index: int, to_index: Optional[int], reason: str
    ) -> None:
        """Insert a loss record."""
        with self.connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO losses (session_id, sensor, from_index, to_index, reason)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, sensor, from_index, to_index, reason),
            )

    def insert_losses_batch(
        self, session_id: str, sensor: int, losses: List[Dict[str, Any]]
    ) -> None:
        """Insert multiple loss records reported in mock reply."""
        if not losses:
            return
        rows = [
            (
                session_id,
                sensor,
                int(loss["from_index"]),
                int(loss["to_index"]) if loss.get("to_index") is not None else None,
                str(loss.get("reason", "unknown")),
            )
            for loss in losses
        ]
        with self.connection() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO losses (session_id, sensor, from_index, to_index, reason)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )

    def get_losses_for_sensor(self, session_id: str, sensor: int) -> List[Dict[str, Any]]:
        """Retrieve all recorded loss records for a given session and sensor."""
        try:
            with self.connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT from_index, to_index, reason 
                    FROM losses 
                    WHERE session_id = ? AND sensor = ?
                    ORDER BY from_index ASC
                    """,
                    (session_id, sensor),
                )
                return [
                    {
                        "from_index": int(row["from_index"]),
                        "to_index": int(row["to_index"]) if row["to_index"] is not None else None,
                        "reason": str(row["reason"]),
                    }
                    for row in cursor
                ]
        except (sqlite3.Error, OSError):
            return []

    def get_last_sample_index(self, session_id: str, sensor: int) -> int:
        """Get highest sample_index stored for the session and sensor."""
        try:
            with self.connection() as conn:
                row = conn.execute(
                    """
                    SELECT MAX(sample_index) as max_idx 
                    FROM samples 
                    WHERE session_id = ? AND sensor = ?
                    """,
                    (session_id, sensor),
                ).fetchone()
                if row and row["max_idx"] is not None:
                    return int(row["max_idx"])
                return -1
        except (sqlite3.Error, OSError):
            return -1

    def get_last_synced_index(self, session_id: str, sensor: int) -> int:
        try:
            with self.connection() as conn:
                row = conn.execute(
                    """
                    SELECT last_synced_index 
                    FROM sync_state 
                    WHERE session_id = ? AND sensor = ?
                    """,
                    (session_id, sensor),
                ).fetchone()
                if row and row["last_synced_index"] is not None:
                    return int(row["last_synced_index"])
                return -1
        except (sqlite3.Error, OSError):
            return -1

    def update_last_synced_index(self, session_id: str, sensor: int, synced_index: int) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (session_id, sensor, last_synced_index)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id, sensor) DO UPDATE SET
                    last_synced_index = MAX(sync_state.last_synced_index, excluded.last_synced_index)
                """,
                (session_id, sensor, synced_index),
            )

    def get_unsynced_samples(
        self, session_id: str, sensor: int, from_index: int, limit: int
    ) -> List[Tuple[int, List[int]]]:
        """Fetch unsynced samples starting strictly after from_index."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT sample_index, values_json 
                FROM samples 
                WHERE session_id = ? AND sensor = ? AND sample_index > ?
                ORDER BY sample_index ASC 
                LIMIT ?
                """,
                (session_id, sensor, from_index, limit),
            )
            return [(row["sample_index"], json.loads(row["values_json"])) for row in cursor]

    def get_samples_range(
        self, session_id: str, sensor: int, start_index: int, end_index: int
    ) -> Dict[int, List[int]]:
        """Fetch samples for [start_index, end_index] inclusive as a dict {sample_index: values}."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT sample_index, values_json 
                FROM samples 
                WHERE session_id = ? AND sensor = ? AND sample_index >= ? AND sample_index <= ?
                ORDER BY sample_index ASC
                """,
                (session_id, sensor, start_index, end_index),
            )
            return {row["sample_index"]: json.loads(row["values_json"]) for row in cursor}

    def save_result(self, session_id: str, window: int, mode: str, data: Dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO results (session_id, window, mode, data_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id, window, mode) DO UPDATE SET
                    data_json = excluded.data_json
                """,
                (session_id, window, mode, json.dumps(data, separators=(",", ":"))),
            )

    def get_results(self, session_id: str) -> List[Dict[str, Any]]:
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT window, mode, data_json 
                FROM results 
                WHERE session_id = ? 
                ORDER BY window ASC, mode ASC
                """,
                (session_id,),
            )
            out = []
            for row in cursor:
                d = json.loads(row["data_json"])
                d["window"] = row["window"]
                d["mode"] = row["mode"]
                out.append(d)
            return out

    def get_max_processed_window(self, session_id: str, mode: str = "online") -> int:
        """Get highest window index already processed and saved for session and mode."""
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT MAX(window) as max_w 
                FROM results 
                WHERE session_id = ? AND mode = ?
                """,
                (session_id, mode),
            ).fetchone()
            if row and row["max_w"] is not None:
                return int(row["max_w"])
            return -1

    def get_latest_session_id(self) -> Optional[str]:
        try:
            with self.connection() as conn:
                row = conn.execute(
                    """
                    SELECT session_id 
                    FROM samples 
                    ORDER BY rowid DESC 
                    LIMIT 1
                    """
                ).fetchone()
                if row:
                    return row["session_id"]
                row = conn.execute(
                    """
                    SELECT session_id 
                    FROM losses 
                    ORDER BY rowid DESC 
                    LIMIT 1
                    """
                ).fetchone()
                return row["session_id"] if row else None
        except (sqlite3.Error, OSError):
            return None

    def get_total_samples_saved(self, session_id: Optional[str] = None) -> int:
        try:
            with self.connection() as conn:
                if session_id:
                    row = conn.execute(
                        "SELECT COUNT(*) as cnt FROM samples WHERE session_id = ?", (session_id,)
                    ).fetchone()
                else:
                    row = conn.execute("SELECT COUNT(*) as cnt FROM samples").fetchone()
                return int(row["cnt"]) if row else 0
        except (sqlite3.Error, OSError):
            return 0

    def get_all_samples_for_export(self, session_id: str):
        """Generator for sample export ordered by sample_index, sensor."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT sensor, sample_index, values_json 
                FROM samples 
                WHERE session_id = ? 
                ORDER BY sample_index ASC, sensor ASC
                """,
                (session_id,),
            )
            for row in cursor:
                yield {
                    "sensor": row["sensor"],
                    "index": row["sample_index"],
                    "values": json.loads(row["values_json"]),
                }

    def get_all_losses_for_export(self, session_id: str):
        """Generator for losses export."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT sensor, from_index, to_index, reason 
                FROM losses 
                WHERE session_id = ? 
                ORDER BY rowid ASC
                """,
                (session_id,),
            )
            for row in cursor:
                yield {
                    "sensor": row["sensor"],
                    "from_index": row["from_index"],
                    "to_index": row["to_index"],
                    "reason": row["reason"],
                }
