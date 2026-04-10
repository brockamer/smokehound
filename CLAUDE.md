# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

```bash
make dev          # Create venv + install with dev + speedtest extras
make test         # Run pytest
make test-cov     # Run pytest with HTML coverage report
make lint         # ruff check + ruff format --check (must both pass for CI)
make format       # ruff check --fix + ruff format (auto-fixes)
make clean        # Remove build/cache artifacts
```

Run a single test file or test by name:
```bash
.venv/bin/pytest tests/test_db.py -v
.venv/bin/pytest tests/test_collectors.py::test_ping_result_fields -v
```

Activate the venv before running `smokehound` directly:
```bash
source .venv/bin/activate
smokehound doctor   # verify system capabilities
smokehound start    # foreground collection
smokehound report   # generate HTML report → ~/.smokehound/reports/
```

## CI

CI runs on push/PR to `main`/`develop`. Two jobs:
- **Lint**: `ruff check` + `ruff format --check` on Python 3.12
- **Test**: pytest matrix across Python 3.10/3.11/3.12 × ubuntu/macos

Both must pass. The most common CI failures are formatting drift (`make format` to fix) and import errors on Python 3.10 (stdlib `tomllib` is 3.11+; the PyPI backport is `tomli`, not `tomllib`).

## Architecture

The codebase is a single-asyncio-loop network monitoring tool. Here's how the layers fit together:

**Collection loop** (`engine.py` → `Engine`)
- Single asyncio event loop, no threads. `Engine.start()` calls `_run_loop()` which calls `_run_cycle()` every `interval_seconds`.
- Each cycle: ping all targets concurrently (via `asyncio.gather`), then DNS round, HTTP round, WiFi collect. Traceroute and speedtest run on longer periodic intervals checked via `time.time()` deltas.
- Sleep/wake detection: if the gap between cycles exceeds 5 minutes, it logs a `sleep_wake` event.
- Outage state machine (`outage.py` → `OutageState`) receives ping results each cycle. It opens an outage record when avg loss ≥ threshold, closes it on recovery, and deletes it if shorter than `duration_threshold_seconds` (blip filtering).

**Collectors** (`collectors/`)
- `ping.py`: Runs system `ping` binary via `asyncio.create_subprocess_exec`, parses stdout. Falls back to TCP connect if ICMP unavailable (`can_use_icmp()` probes this at startup).
- `dns.py`: Uses `aiodns` for non-system resolvers, `socket.getaddrinfo` for `"system"` resolver.
- `http.py`: Uses `aiohttp` with manual timing hooks to capture DNS, TCP connect, TLS handshake, and TTFB separately.
- `gateway.py`: Detects default gateway via `netstat -rn` (macOS) or `ip route` (Linux); reads WiFi RSSI/noise/channel via `airport` (macOS) or `iw`/`iwconfig` (Linux).
- `traceroute.py`: Runs `traceroute` binary, parses hops with `_parse_traceroute()`, computes a path hash to detect routing changes.
- `speedtest.py`: Uses Cloudflare's speed test endpoint (HTTP download timing), not `speedtest-cli`.

**Storage** (`db.py` → `Database`)
- SQLite in WAL mode with FK enforcement. Schema is in `SCHEMA_SQL` at the top of `db.py`. Schema version tracked in `metadata` table.
- All queries return `sqlite3.Row` objects (dict-like). `fetchall()` / `fetchone()` are thin wrappers.
- `transaction()` is a `@contextmanager` that wraps a connection; exceptions trigger rollback.
- All time values are Unix timestamps (float seconds), stored as `REAL`.

**Configuration** (`config.py`)
- Dataclass hierarchy: `Config` holds typed sub-configs (`PingConfig`, `DnsConfig`, etc.).
- `load_config()` deep-merges user TOML over `DEFAULT_CONFIG` dict, then constructs typed dataclasses.
- `Config.db_path`, `Config.log_dir`, etc. are derived `@property` values from `config.data_dir`.
- On Python 3.10, `tomllib` is not available in stdlib — `tomli` (PyPI) is imported as fallback.

**Report** (`report.py`)
- Generates a single self-contained HTML file with inline Plotly.js (no CDN dependency).
- The JS `LAYOUT_BASE` constant defines shared chart styling (dark theme, `height: 360`). Charts are created with `{...LAYOUT_BASE, ...overrides}` — later keys win over the spread, so `height: 120` on the timeline chart overrides the base 360.
- Do NOT add `autosize: true` to `LAYOUT_BASE` — it causes Plotly to read the container's CSS height (~24px) instead of the explicit `height` value, collapsing all charts without their own height override.
- The `docs/sample-report.html` is a pre-generated demo with simulated data. Regenerate it with the fixture script after any report.py changes.

**CLI** (`cli.py`)
- Click group with subcommands: `start`, `stop`, `status`, `report`, `tail`, `export`, `doctor`, `config`, `reset`.
- Daemon mode uses `os.fork()` — macOS/Linux only.
- `smokehound tail` polls the DB every 5s (does not tap the live engine).
