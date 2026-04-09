# SmokеHound

**Network diagnostics collector and visualizer.** Designed to run on your laptop for days to characterize intermittent WiFi and internet issues — survives sleep/wake cycles, network drops, and lid closes.

## Quick Start

```bash
git clone https://github.com/smokehound/smokehound
cd smokehound
make install
source .venv/bin/activate

smokehound doctor        # Verify your system can run all probes
smokehound start         # Start collecting (foreground; Ctrl+C to stop)
# Wait a few hours...
smokehound report        # Generate an HTML report and open it in your browser
```

## What It Measures

Every 30 seconds (configurable):

| Metric | What it tells you |
|--------|-------------------|
| **ICMP Ping** to gateway, 1.1.1.1, 8.8.8.8 | RTT min/avg/max, jitter, packet loss |
| **DNS Resolution** across 4 resolvers | Resolution speed, failures, hijacking detection |
| **HTTP(S) Latency** to Google, Apple, Ubuntu | DNS time, TCP connect, TLS handshake, TTFB |
| **WiFi Signal** (RSSI, noise, channel) | WiFi layer health separate from internet |

Every 10 minutes:
| **Traceroute** to 8.8.8.8 | Detect routing changes |

Every 30 minutes:
| **Speed test** (Cloudflare) | Download/upload Mbps |

## CLI Reference

```
smokehound start              Start collecting (foreground, Ctrl+C to stop)
smokehound start -d           Start as background daemon
smokehound stop               Stop background daemon
smokehound status             Show run status, DB size, measurement count
smokehound report             Generate HTML report, last 24h (opens in browser)
smokehound report --last 4h   Report for last 4 hours
smokehound report --last 7d   Report for last 7 days
smokehound report --from "2025-01-15 08:00" --to "2025-01-15 18:00"
smokehound tail               Live tail of measurements
smokehound export             Export ping data to CSV
smokehound doctor             Check system capabilities
smokehound config             Print current config
smokehound reset              Wipe database (with confirmation)
```

## Architecture

```
~/.smokehound/
├── smokehound.db       # SQLite database (WAL mode)
├── config.toml         # Configuration
├── smokehound.pid      # PID file (daemon mode only)
├── logs/               # Log files
└── reports/            # Generated HTML reports
```

**Stack:** Python 3.10+ · asyncio · SQLite · Rich (CLI) · Plotly (reports)

The engine runs as a single asyncio event loop — no threads, no subprocesses per measurement. The ping collector uses your system `ping` binary; all other probes are pure Python async. SQLite WAL mode ensures crash safety.

## Configuration

Default config is written to `~/.smokehound/config.toml` on first run. Edit it to customize:

```toml
[general]
interval_seconds = 30       # How often to measure (seconds)
data_dir = "~/.smokehound"

[ping]
targets = ["gateway", "1.1.1.1", "8.8.8.8"]
count = 5                   # Pings per cycle
timeout_seconds = 5

[dns]
domains = ["google.com", "cloudflare.com", "github.com"]
resolvers = ["system", "1.1.1.1", "8.8.8.8", "9.9.9.9"]
timeout_seconds = 5

[http]
targets = [
    "https://www.google.com/generate_204",
    "https://captive.apple.com/hotspot-detect.html",
]
timeout_seconds = 10

[traceroute]
target = "8.8.8.8"
interval_minutes = 10

[speedtest]
enabled = true
interval_minutes = 30       # Keep this infrequent to avoid polluting results

[outage]
loss_threshold_percent = 50  # Loss% that triggers outage detection
duration_threshold_seconds = 60  # Minimum duration to count as an outage
```

## The Report

`smokehound report` generates a single self-contained HTML file you can open offline. It includes:

- **Status timeline** — full-width green/red bar showing uptime/downtime
- **Ping RTT** — time series with min/avg/max bands per target
- **Packet Loss** — highlighted when >0%
- **Jitter** — RTT standard deviation over time
- **DNS Performance** — resolution times per resolver, failure markers
- **HTTP Latency Breakdown** — stacked DNS / TCP / TLS / TTFB
- **WiFi Signal** — RSSI and noise over time (macOS and Linux)
- **Bandwidth** — speed test scatter plot
- **Outage Log** — table with start/end/duration/max-loss for each outage
- **Summary Cards** — uptime %, avg RTT, p95 RTT, total outages, avg bandwidth

All charts are interactive (zoom, pan, hover).

## Troubleshooting

**`ping` doesn't work / shows permission error**

SmokеHound falls back to TCP ping automatically. To use ICMP ping, either run as root or set the `setuid` bit on ping (macOS does this by default).

**WiFi metrics show "N/A"**

On macOS, the `airport` utility is required. It's bundled with macOS but the path may change between versions. Run `smokehound doctor` to check.

On Linux, install `iw` or `iwconfig` (`sudo apt install iw wireless-tools`).

**Speed test is very slow**

The Cloudflare speed test downloads 10 MB. If your connection is slow, increase `[speedtest] interval_minutes` or set `enabled = false`.

**The daemon won't stop**

Find the PID manually: `cat ~/.smokehound/smokehound.pid && kill <pid>`

**Database is corrupt**

Run `smokehound reset` to wipe and start fresh. SmokеHound uses SQLite WAL mode to minimize corruption risk, but a hard crash during a write is always possible.

## Sample Report

**[View live sample report →](https://htmlpreview.github.io/?https://github.com/brockamer/smokehound/blob/main/docs/sample-report.html)**

The sample shows 4 hours of simulated data including two outages, a degradation period with high jitter and packet loss, WiFi RSSI drops, and a traceroute path change. All charts are interactive — zoom, pan, and hover for details.

## Development

```bash
make dev          # Set up dev environment with test dependencies
make test         # Run tests
make lint         # Run ruff
make format       # Format with ruff
make clean        # Remove build artifacts
```

## License

MIT — see [LICENSE](LICENSE).
