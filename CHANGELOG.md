# Changelog

All notable changes to SmokеHound will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-04-08

### Added

- Initial release
- ICMP ping collector with TCP fallback — RTT min/avg/max, jitter, packet loss
- DNS resolution collector — system resolver + 1.1.1.1, 8.8.8.8, 9.9.9.9
- HTTP(S) latency collector with timing breakdown (DNS / connect / TLS / TTFB)
- Traceroute collector with path-change detection
- WiFi signal collector — RSSI, noise, channel (macOS `airport`, Linux `iw`/`iwconfig`)
- Speed test — Cloudflare fallback or `speedtest-cli` if available
- Outage detection — configurable loss threshold and duration
- Sleep/wake detection — logs gaps > 5 minutes
- SQLite database with WAL mode for crash safety
- Rich CLI — `start`, `stop`, `status`, `report`, `tail`, `export`, `doctor`, `config`, `reset`
- Daemon mode (`start -d`) with PID file management
- Interactive HTML reports via Plotly — dark theme, all charts, outage log, summary cards
- Configurable via `~/.smokehound/config.toml` with sensible defaults
- `smokehound doctor` — system capability check
- pytest test suite covering schema, collectors, report generation, CLI
- GitHub Actions CI — lint + test on Python 3.10/3.11/3.12, macOS + Linux
