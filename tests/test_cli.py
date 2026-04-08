"""Tests that CLI commands don't crash."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from smokehound.cli import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def tmp_config(tmp_path):
    """A minimal config pointing to a temp data dir."""
    config = tmp_path / "config.toml"
    data_dir = tmp_path / "data"
    config.write_text(f'[general]\ndata_dir = "{data_dir}"\n')
    return config


def test_main_help(runner):
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "network diagnostics" in result.output.lower()


def test_version(runner):
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_config_command(runner, tmp_config):
    result = runner.invoke(main, ["config", "-c", str(tmp_config)])
    assert result.exit_code == 0
    assert "interval_seconds" in result.output


def test_status_no_db(runner, tmp_config):
    """Status command doesn't crash when no DB exists."""
    result = runner.invoke(main, ["status", "-c", str(tmp_config)])
    assert result.exit_code == 0


def test_doctor_command(runner):
    """Doctor command runs and exits cleanly."""
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 0
    assert "Python version" in result.output


def test_reset_no_confirm(runner, tmp_config):
    """Reset command aborts without -y."""
    result = runner.invoke(main, ["reset", "-c", str(tmp_config)], input="n\n")
    assert result.exit_code != 0 or "Aborted" in result.output


def test_reset_with_yes(runner, tmp_config):
    """Reset with -y on nonexistent DB doesn't crash."""
    result = runner.invoke(main, ["reset", "-y", "-c", str(tmp_config)])
    assert result.exit_code == 0


def test_export_no_db(runner, tmp_config):
    """Export on missing DB exits with error message."""
    result = runner.invoke(main, ["export", "-c", str(tmp_config)])
    assert result.exit_code != 0 or "No database" in result.output


def test_report_no_db(runner, tmp_config):
    """Report on missing DB exits with error message."""
    result = runner.invoke(main, ["report", "-c", str(tmp_config)])
    assert result.exit_code != 0 or "No database" in result.output
