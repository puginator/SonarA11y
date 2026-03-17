from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .contracts import FixResult


class RemediationCache:
    def __init__(self, path: str, enabled: bool = True) -> None:
        self._path = Path(path)
        self._enabled = enabled
        self._lock = threading.Lock()
        if self._enabled:
            self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS remediation_cache (
                        cache_key TEXT PRIMARY KEY,
                        result_json TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        last_accessed_at REAL NOT NULL,
                        hit_count INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                connection.commit()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def path(self) -> str:
        return str(self._path)

    def get(self, cache_key: str) -> FixResult | None:
        if not self._enabled:
            return None

        now = time.time()
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT result_json FROM remediation_cache WHERE cache_key = ?",
                    (cache_key,),
                ).fetchone()
                if row is None:
                    return None
                connection.execute(
                    """
                    UPDATE remediation_cache
                    SET last_accessed_at = ?, hit_count = hit_count + 1
                    WHERE cache_key = ?
                    """,
                    (now, cache_key),
                )
                connection.commit()
        return FixResult.model_validate(json.loads(row["result_json"]))

    def put(self, cache_key: str, result: FixResult) -> None:
        if not self._enabled:
            return

        now = time.time()
        payload = json.dumps(result.model_dump(mode="json"), sort_keys=True)
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO remediation_cache (
                        cache_key,
                        result_json,
                        created_at,
                        updated_at,
                        last_accessed_at,
                        hit_count
                    )
                    VALUES (?, ?, ?, ?, ?, 0)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        result_json = excluded.result_json,
                        updated_at = excluded.updated_at,
                        last_accessed_at = excluded.last_accessed_at
                    """,
                    (cache_key, payload, now, now, now),
                )
                connection.commit()

    def stats(self) -> dict[str, int | str | bool]:
        if not self._enabled:
            return {
                "enabled": False,
                "path": str(self._path),
                "entries": 0,
                "totalHits": 0,
            }

        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT COUNT(*) AS entries, COALESCE(SUM(hit_count), 0) AS total_hits
                    FROM remediation_cache
                    """
                ).fetchone()
        return {
            "enabled": True,
            "path": str(self._path),
            "entries": int(row["entries"]) if row else 0,
            "totalHits": int(row["total_hits"]) if row else 0,
        }
