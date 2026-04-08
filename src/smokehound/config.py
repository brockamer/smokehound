"""Configuration loading and defaults for SmokеHound."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

import tomli_w

DEFAULT_DATA_DIR = Path.home() / ".smokehound"
DEFAULT_CONFIG_PATH = DEFAULT_DATA_DIR / "config.toml"

DEFAULT_CONFIG: dict = {
    "general": {
        "interval_seconds": 30,
        "data_dir": str(DEFAULT_DATA_DIR),
    },
    "ping": {
        "targets": ["gateway", "1.1.1.1", "8.8.8.8"],
        "count": 5,
        "timeout_seconds": 5,
    },
    "dns": {
        "domains": ["google.com", "cloudflare.com", "github.com"],
        "resolvers": ["system", "1.1.1.1", "8.8.8.8", "9.9.9.9"],
        "timeout_seconds": 5,
    },
    "http": {
        "targets": [
            "https://www.google.com/generate_204",
            "https://captive.apple.com/hotspot-detect.html",
        ],
        "timeout_seconds": 10,
    },
    "traceroute": {
        "target": "8.8.8.8",
        "interval_minutes": 10,
    },
    "speedtest": {
        "enabled": True,
        "interval_minutes": 30,
    },
    "report": {
        "theme": "dark",
        "default_window": "24h",
    },
    "outage": {
        "loss_threshold_percent": 50,
        "duration_threshold_seconds": 60,
    },
}


@dataclass
class GeneralConfig:
    interval_seconds: int = 30
    data_dir: Path = field(default_factory=lambda: DEFAULT_DATA_DIR)


@dataclass
class PingConfig:
    targets: list[str] = field(default_factory=lambda: ["gateway", "1.1.1.1", "8.8.8.8"])
    count: int = 5
    timeout_seconds: int = 5


@dataclass
class DnsConfig:
    domains: list[str] = field(
        default_factory=lambda: ["google.com", "cloudflare.com", "github.com"]
    )
    resolvers: list[str] = field(
        default_factory=lambda: ["system", "1.1.1.1", "8.8.8.8", "9.9.9.9"]
    )
    timeout_seconds: int = 5


@dataclass
class HttpConfig:
    targets: list[str] = field(
        default_factory=lambda: [
            "https://www.google.com/generate_204",
            "https://captive.apple.com/hotspot-detect.html",
        ]
    )
    timeout_seconds: int = 10


@dataclass
class TracerouteConfig:
    target: str = "8.8.8.8"
    interval_minutes: int = 10


@dataclass
class SpeedtestConfig:
    enabled: bool = True
    interval_minutes: int = 30


@dataclass
class ReportConfig:
    theme: str = "dark"
    default_window: str = "24h"


@dataclass
class OutageConfig:
    loss_threshold_percent: float = 50.0
    duration_threshold_seconds: int = 60


@dataclass
class Config:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    ping: PingConfig = field(default_factory=PingConfig)
    dns: DnsConfig = field(default_factory=DnsConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    traceroute: TracerouteConfig = field(default_factory=TracerouteConfig)
    speedtest: SpeedtestConfig = field(default_factory=SpeedtestConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    outage: OutageConfig = field(default_factory=OutageConfig)

    @property
    def data_dir(self) -> Path:
        return self.general.data_dir

    @property
    def db_path(self) -> Path:
        return self.data_dir / "smokehound.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def report_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def pid_file(self) -> Path:
        return self.data_dir / "smokehound.pid"


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_path: Path | None = None) -> Config:
    """Load config from TOML file, merging with defaults."""
    path = config_path or DEFAULT_CONFIG_PATH
    raw = dict(DEFAULT_CONFIG)

    if path.exists():
        with open(path, "rb") as f:
            user_config = tomllib.load(f)
        raw = _deep_merge(raw, user_config)

    g = raw.get("general", {})
    data_dir = Path(g.get("data_dir", str(DEFAULT_DATA_DIR))).expanduser()

    config = Config(
        general=GeneralConfig(
            interval_seconds=int(g.get("interval_seconds", 30)),
            data_dir=data_dir,
        ),
        ping=PingConfig(**raw.get("ping", {})),
        dns=DnsConfig(**raw.get("dns", {})),
        http=HttpConfig(**raw.get("http", {})),
        traceroute=TracerouteConfig(**raw.get("traceroute", {})),
        speedtest=SpeedtestConfig(**raw.get("speedtest", {})),
        report=ReportConfig(**raw.get("report", {})),
        outage=OutageConfig(**raw.get("outage", {})),
    )
    return config


def write_default_config(path: Path) -> None:
    """Write default config.toml to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(DEFAULT_CONFIG, f)


def ensure_data_dirs(config: Config) -> None:
    """Create all required data directories."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.log_dir.mkdir(parents=True, exist_ok=True)
    config.report_dir.mkdir(parents=True, exist_ok=True)
