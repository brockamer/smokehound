"""Tests for config loading."""



from smokehound.config import (
    DEFAULT_CONFIG,
    load_config,
    write_default_config,
)


def test_load_config_defaults(tmp_path):
    """Config loads with all defaults when no file exists."""
    cfg = load_config(tmp_path / "nonexistent.toml")
    assert cfg.general.interval_seconds == 30
    assert "1.1.1.1" in cfg.ping.targets
    assert "google.com" in cfg.dns.domains
    assert cfg.speedtest.enabled is True
    assert cfg.outage.loss_threshold_percent == 50.0


def test_load_config_override(tmp_path):
    """User config overrides defaults."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[general]\ninterval_seconds = 60\n\n[ping]\ncount = 10\n"
    )
    cfg = load_config(config_file)
    assert cfg.general.interval_seconds == 60
    assert cfg.ping.count == 10
    # Non-overridden values stay as defaults
    assert "1.1.1.1" in cfg.ping.targets


def test_write_default_config(tmp_path):
    """Default config file is written and readable."""
    path = tmp_path / "config.toml"
    write_default_config(path)
    assert path.exists()
    cfg = load_config(path)
    assert cfg.general.interval_seconds == DEFAULT_CONFIG["general"]["interval_seconds"]


def test_config_paths(tmp_path):
    """Config path properties resolve correctly."""
    cfg = load_config(tmp_path / "nonexistent.toml")
    cfg.general.data_dir = tmp_path / "data"
    assert cfg.db_path == tmp_path / "data" / "smokehound.db"
    assert cfg.log_dir == tmp_path / "data" / "logs"
    assert cfg.report_dir == tmp_path / "data" / "reports"


def test_deep_merge():
    """Deep merge preserves nested defaults."""
    from smokehound.config import _deep_merge

    base = {"a": {"x": 1, "y": 2}, "b": 3}
    override = {"a": {"x": 10}}
    result = _deep_merge(base, override)
    assert result["a"]["x"] == 10
    assert result["a"]["y"] == 2
    assert result["b"] == 3
