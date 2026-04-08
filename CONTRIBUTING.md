# Contributing to SmokеHound

Thanks for wanting to contribute. SmokеHound is a focused tool — new collectors and report improvements are the most valuable areas.

## Setup

```bash
git clone https://github.com/smokehound/smokehound
cd smokehound
make dev
source .venv/bin/activate
```

## Running tests

```bash
make test           # Run the full suite
make lint           # Check style
make format         # Auto-format
```

Tests should pass before submitting a PR. Network-dependent tests are skipped gracefully in CI.

## Commit format

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add per-hop latency breakdown to traceroute chart
fix: handle airport utility path change on macOS 15
docs: add troubleshooting section for WiFi metrics
test: add fixture for DNS timeout responses
```

## Adding a new collector

1. Create `src/smokehound/collectors/my_collector.py`
2. Add a `collect_*` async function returning a `dict[str, Any]` with a `ts` key
3. Add the corresponding table and indexes in `db.py`
4. Add an `insert_*` method in `db.py`
5. Wire it into `engine.py` (check how ping/dns/http are called)
6. Add a chart in `report.py`
7. Add tests in `tests/test_collectors.py`

## Adding a new chart

Charts live in `report.py` → `_render_html()`. Each chart is a self-contained JavaScript block using Plotly. Follow the existing pattern — `RAW` contains all data, layout inherits from `LAYOUT_BASE`.

## What we don't want

- External web service dependencies (no speedtest APIs requiring signup)
- Background threads (stay in asyncio)
- Breaking changes to the DB schema without a migration path
- Features that increase memory usage beyond ~50 MB

## Questions

Open a GitHub Discussion or file an issue with the `question` label.
