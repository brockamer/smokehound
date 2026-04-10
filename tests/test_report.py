"""Tests for report generation."""

from __future__ import annotations

import time

import pytest

from smokehound.db import Database
from smokehound.report import _fmt_duration, generate_report
from smokehound.utils import parse_window as _parse_window


@pytest.fixture
def populated_db(tmp_path):
    """DB with fixture data for report testing."""
    db = Database(tmp_path / "test.db")
    db.connect()
    run_id = db.start_run(pid=1, version="0.1.0")

    now = time.time()
    # Insert 5 ping measurements
    for i in range(5):
        db.insert_ping(
            run_id,
            {
                "ts": now - (5 - i) * 30,
                "target": "8.8.8.8",
                "rtt_min_ms": 10.0 + i,
                "rtt_avg_ms": 12.0 + i,
                "rtt_max_ms": 15.0 + i,
                "jitter_ms": 1.5,
                "loss_pct": 0.0,
                "packets_sent": 5,
                "packets_recv": 5,
                "error": None,
            },
        )

    # Insert DNS results
    db.insert_dns(
        run_id,
        {
            "ts": now - 60,
            "domain": "google.com",
            "resolver": "system",
            "resolve_ms": 20.0,
            "status": "ok",
            "resolved_ip": "142.250.80.46",
            "error": None,
        },
    )

    # Insert HTTP result
    db.insert_http(
        run_id,
        {
            "ts": now - 60,
            "target": "https://www.google.com/generate_204",
            "status_code": 204,
            "dns_ms": 5.0,
            "connect_ms": 10.0,
            "tls_ms": 15.0,
            "ttfb_ms": 20.0,
            "total_ms": 50.0,
            "error": None,
        },
    )

    # One outage event
    outage_id = db.open_outage(run_id, now - 200, "loss=100%")
    db.close_outage(outage_id, now - 140, 100.0)

    db.end_run(run_id)
    return db


def test_generate_report_creates_file(populated_db, tmp_path):
    """Report HTML file is created."""
    out = generate_report(
        populated_db,
        since=time.time() - 3600,
        until=time.time(),
        output_path=tmp_path / "report.html",
    )
    assert out.exists()
    content = out.read_text()
    assert "SmokеHound" in content
    assert "plotly" in content.lower()


def test_report_contains_charts(populated_db, tmp_path):
    """Report HTML contains expected chart divs."""
    out = generate_report(
        populated_db,
        since=time.time() - 3600,
        until=time.time(),
        output_path=tmp_path / "report.html",
    )
    content = out.read_text()
    assert "chart-ping" in content
    assert "chart-loss" in content
    assert "chart-dns" in content
    assert "chart-timeline" in content


def test_report_outage_table(populated_db, tmp_path):
    """Report includes outage data."""
    out = generate_report(
        populated_db,
        since=time.time() - 3600,
        until=time.time(),
        output_path=tmp_path / "report.html",
    )
    content = out.read_text()
    assert "Outage Log" in content
    assert "loss=100%" in content


def test_compute_summary_with_data(populated_db):
    """Summary stats computed correctly."""
    now = time.time()
    since = now - 3600
    where = f"ts >= {since} AND ts <= {now}"
    from smokehound.report import _collect_data

    data = _collect_data(populated_db, where, since, now)
    s = data["summary"]
    assert s["avg_rtt_ms"] is not None
    assert s["avg_rtt_ms"] > 0
    assert s["uptime_pct"] <= 100.0
    assert s["total_outages"] == 1


def test_fmt_duration():
    assert _fmt_duration(30) == "30s"
    assert _fmt_duration(90) == "1.5m"
    assert _fmt_duration(7200) == "2.0h"


def test_parse_window():
    assert _parse_window("4h") == 4 * 3600
    assert _parse_window("24h") == 24 * 3600
    assert _parse_window("7d") == 7 * 86400
    assert _parse_window("30m") == 30 * 60


def test_parse_window_invalid():
    import click

    with pytest.raises(click.BadParameter):
        _parse_window("invalid")
