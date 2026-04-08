"""Tests for database schema and operations."""

import time

import pytest

from smokehound.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "test.db")
    d.connect()
    yield d
    d.close()


def test_schema_creation(db):
    """All expected tables are created."""
    tables = {
        row[0]
        for row in db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    expected = {
        "metadata", "runs", "ping_results", "dns_results", "http_results",
        "traceroute_results", "wifi_results", "speedtest_results",
        "outage_events", "system_events",
    }
    assert expected.issubset(tables)


def test_metadata_populated(db):
    """Schema version and first_run are set."""
    row = db.fetchone("SELECT value FROM metadata WHERE key='schema_version'")
    assert row is not None
    assert row["value"] == "1"


def test_run_lifecycle(db):
    """Start and end a run."""
    run_id = db.start_run(pid=12345, version="0.1.0")
    assert run_id > 0

    run = db.get_run(run_id)
    assert run is not None
    assert run["pid"] == 12345
    assert run["ended_at"] is None

    db.end_run(run_id)
    run = db.get_run(run_id)
    assert run["ended_at"] is not None


def test_insert_ping(db):
    """Ping results are inserted and retrievable."""
    run_id = db.start_run(12345, "0.1.0")
    data = {
        "ts": time.time(),
        "target": "8.8.8.8",
        "rtt_min_ms": 10.0,
        "rtt_avg_ms": 12.5,
        "rtt_max_ms": 15.0,
        "jitter_ms": 2.0,
        "loss_pct": 0.0,
        "packets_sent": 5,
        "packets_recv": 5,
        "error": None,
    }
    db.insert_ping(run_id, data)
    rows = db.fetchall("SELECT * FROM ping_results WHERE run_id = ?", (run_id,))
    assert len(rows) == 1
    assert rows[0]["target"] == "8.8.8.8"
    assert rows[0]["rtt_avg_ms"] == 12.5


def test_insert_dns(db):
    run_id = db.start_run(12345, "0.1.0")
    data = {
        "ts": time.time(),
        "domain": "google.com",
        "resolver": "1.1.1.1",
        "resolve_ms": 25.0,
        "status": "ok",
        "resolved_ip": "142.250.80.46",
        "error": None,
    }
    db.insert_dns(run_id, data)
    rows = db.fetchall("SELECT * FROM dns_results WHERE run_id = ?", (run_id,))
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"


def test_outage_lifecycle(db):
    """Outage open/close works correctly."""
    run_id = db.start_run(12345, "0.1.0")
    ts_start = time.time()
    outage_id = db.open_outage(run_id, ts_start, "loss=100%")
    assert outage_id > 0

    ts_end = ts_start + 120
    db.close_outage(outage_id, ts_end, 100.0)

    row = db.fetchone("SELECT * FROM outage_events WHERE id = ?", (outage_id,))
    assert row["duration_s"] == pytest.approx(120.0, abs=1.0)
    assert row["max_loss_pct"] == 100.0


def test_system_event(db):
    run_id = db.start_run(12345, "0.1.0")
    db.log_event(run_id, "test_event", "detail here")
    rows = db.fetchall("SELECT * FROM system_events WHERE kind = 'test_event'")
    assert len(rows) == 1
    assert rows[0]["detail"] == "detail here"


def test_wal_mode(db):
    """WAL journal mode is enabled."""
    row = db.fetchone("PRAGMA journal_mode")
    assert row[0] == "wal"


def test_stats(db):
    """get_stats returns expected shape."""
    stats = db.get_stats()
    assert "ping_count" in stats
    assert "db_size_bytes" in stats
    assert "outage_count" in stats
