"""SQLite database layer for SmokеHound."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    ended_at   REAL,
    pid        INTEGER,
    version    TEXT
);

CREATE TABLE IF NOT EXISTS ping_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    ts          REAL NOT NULL,
    target      TEXT NOT NULL,
    rtt_min_ms  REAL,
    rtt_avg_ms  REAL,
    rtt_max_ms  REAL,
    jitter_ms   REAL,
    loss_pct    REAL NOT NULL,
    packets_sent    INTEGER NOT NULL,
    packets_recv    INTEGER NOT NULL,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS dns_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(id),
    ts              REAL NOT NULL,
    domain          TEXT NOT NULL,
    resolver        TEXT NOT NULL,
    resolve_ms      REAL,
    status          TEXT NOT NULL,
    resolved_ip     TEXT,
    error           TEXT
);

CREATE TABLE IF NOT EXISTS http_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(id),
    ts              REAL NOT NULL,
    target          TEXT NOT NULL,
    status_code     INTEGER,
    dns_ms          REAL,
    connect_ms      REAL,
    tls_ms          REAL,
    ttfb_ms         REAL,
    total_ms        REAL,
    error           TEXT
);

CREATE TABLE IF NOT EXISTS traceroute_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    ts          REAL NOT NULL,
    target      TEXT NOT NULL,
    hop_count   INTEGER,
    path_hash   TEXT,
    hops_json   TEXT,
    changed     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS wifi_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    ts          REAL NOT NULL,
    ssid        TEXT,
    rssi_dbm    REAL,
    noise_dbm   REAL,
    channel     INTEGER,
    link_speed_mbps REAL,
    tx_rate_mbps    REAL,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS speedtest_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(id),
    ts              REAL NOT NULL,
    download_mbps   REAL,
    upload_mbps     REAL,
    ping_ms         REAL,
    server          TEXT,
    error           TEXT
);

CREATE TABLE IF NOT EXISTS outage_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    started_at  REAL NOT NULL,
    ended_at    REAL,
    duration_s  REAL,
    trigger     TEXT NOT NULL,
    max_loss_pct REAL
);

CREATE TABLE IF NOT EXISTS system_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  INTEGER REFERENCES runs(id),
    ts      REAL NOT NULL,
    kind    TEXT NOT NULL,
    detail  TEXT
);

CREATE INDEX IF NOT EXISTS idx_ping_ts       ON ping_results(ts);
CREATE INDEX IF NOT EXISTS idx_ping_run      ON ping_results(run_id);
CREATE INDEX IF NOT EXISTS idx_dns_ts        ON dns_results(ts);
CREATE INDEX IF NOT EXISTS idx_dns_run       ON dns_results(run_id);
CREATE INDEX IF NOT EXISTS idx_http_ts       ON http_results(ts);
CREATE INDEX IF NOT EXISTS idx_http_run      ON http_results(run_id);
CREATE INDEX IF NOT EXISTS idx_traceroute_ts ON traceroute_results(ts);
CREATE INDEX IF NOT EXISTS idx_wifi_ts       ON wifi_results(ts);
CREATE INDEX IF NOT EXISTS idx_speedtest_ts  ON speedtest_results(ts);
CREATE INDEX IF NOT EXISTS idx_outage_start  ON outage_events(started_at);
CREATE INDEX IF NOT EXISTS idx_sysev_ts      ON system_events(ts);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; we manage transactions manually
        )
        self._conn.row_factory = sqlite3.Row
        self._apply_schema()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    def _apply_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        # Set metadata if not present
        self.conn.execute(
            "INSERT OR IGNORE INTO metadata(key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO metadata(key, value) VALUES (?, ?)",
            ("first_run", str(__import__("time").time())),
        )

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        self.conn.execute("BEGIN")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, params_seq: list[tuple]) -> sqlite3.Cursor:
        return self.conn.executemany(sql, params_seq)

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    # --- Run management ---

    def start_run(self, pid: int, version: str) -> int:
        import time

        cur = self.conn.execute(
            "INSERT INTO runs(started_at, pid, version) VALUES (?, ?, ?)",
            (time.time(), pid, version),
        )
        return cur.lastrowid  # type: ignore[return-value]

    def end_run(self, run_id: int) -> None:
        import time

        self.conn.execute(
            "UPDATE runs SET ended_at = ? WHERE id = ?",
            (time.time(), run_id),
        )

    def get_run(self, run_id: int) -> sqlite3.Row | None:
        return self.fetchone("SELECT * FROM runs WHERE id = ?", (run_id,))

    def get_last_run(self) -> sqlite3.Row | None:
        return self.fetchone("SELECT * FROM runs ORDER BY id DESC LIMIT 1")

    # --- Ping ---

    def insert_ping(self, run_id: int, data: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO ping_results
               (run_id, ts, target, rtt_min_ms, rtt_avg_ms, rtt_max_ms, jitter_ms,
                loss_pct, packets_sent, packets_recv, error)
               VALUES (:run_id, :ts, :target, :rtt_min_ms, :rtt_avg_ms, :rtt_max_ms,
                       :jitter_ms, :loss_pct, :packets_sent, :packets_recv, :error)""",
            {"run_id": run_id, **data},
        )

    # --- DNS ---

    def insert_dns(self, run_id: int, data: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO dns_results
               (run_id, ts, domain, resolver, resolve_ms, status, resolved_ip, error)
               VALUES (:run_id, :ts, :domain, :resolver, :resolve_ms, :status,
                       :resolved_ip, :error)""",
            {"run_id": run_id, **data},
        )

    # --- HTTP ---

    def insert_http(self, run_id: int, data: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO http_results
               (run_id, ts, target, status_code, dns_ms, connect_ms, tls_ms,
                ttfb_ms, total_ms, error)
               VALUES (:run_id, :ts, :target, :status_code, :dns_ms, :connect_ms,
                       :tls_ms, :ttfb_ms, :total_ms, :error)""",
            {"run_id": run_id, **data},
        )

    # --- Traceroute ---

    def insert_traceroute(self, run_id: int, data: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO traceroute_results
               (run_id, ts, target, hop_count, path_hash, hops_json, changed)
               VALUES (:run_id, :ts, :target, :hop_count, :path_hash, :hops_json, :changed)""",
            {"run_id": run_id, **data},
        )

    def get_last_traceroute_hash(self, target: str) -> str | None:
        row = self.fetchone(
            "SELECT path_hash FROM traceroute_results WHERE target = ? ORDER BY ts DESC LIMIT 1",
            (target,),
        )
        return row["path_hash"] if row else None

    # --- WiFi ---

    def insert_wifi(self, run_id: int, data: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO wifi_results
               (run_id, ts, ssid, rssi_dbm, noise_dbm, channel, link_speed_mbps,
                tx_rate_mbps, error)
               VALUES (:run_id, :ts, :ssid, :rssi_dbm, :noise_dbm, :channel,
                       :link_speed_mbps, :tx_rate_mbps, :error)""",
            {"run_id": run_id, **data},
        )

    # --- Speedtest ---

    def insert_speedtest(self, run_id: int, data: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO speedtest_results
               (run_id, ts, download_mbps, upload_mbps, ping_ms, server, error)
               VALUES (:run_id, :ts, :download_mbps, :upload_mbps, :ping_ms, :server, :error)""",
            {"run_id": run_id, **data},
        )

    # --- Outage events ---

    def open_outage(self, run_id: int, ts: float, trigger: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO outage_events(run_id, started_at, trigger) VALUES (?, ?, ?)",
            (run_id, ts, trigger),
        )
        return cur.lastrowid  # type: ignore[return-value]

    def close_outage(self, outage_id: int, ts: float, max_loss_pct: float) -> None:
        row = self.fetchone("SELECT started_at FROM outage_events WHERE id = ?", (outage_id,))
        duration = ts - row["started_at"] if row else 0
        self.conn.execute(
            "UPDATE outage_events SET ended_at=?, duration_s=?, max_loss_pct=? WHERE id=?",
            (ts, duration, max_loss_pct, outage_id),
        )

    # --- System events ---

    def log_event(self, run_id: int | None, kind: str, detail: str = "") -> None:
        import time

        self.conn.execute(
            "INSERT INTO system_events(run_id, ts, kind, detail) VALUES (?, ?, ?, ?)",
            (run_id, time.time(), kind, detail),
        )

    # --- Query helpers ---

    def get_stats(self, since: float | None = None) -> dict[str, Any]:
        """Return summary statistics for the status command."""
        where = f"WHERE ts >= {since}" if since else ""
        row = self.fetchone(
            f"SELECT COUNT(*) as n, MIN(ts) as first, MAX(ts) as last FROM ping_results {where}"
        )
        ping_count = row["n"] if row else 0

        db_size = self.path.stat().st_size if self.path.exists() else 0

        outage_row = self.fetchone(
            f"SELECT COUNT(*) as n FROM outage_events {where.replace('ts', 'started_at')}"
        )
        outage_count = outage_row["n"] if outage_row else 0

        return {
            "ping_count": ping_count,
            "db_size_bytes": db_size,
            "outage_count": outage_count,
        }
