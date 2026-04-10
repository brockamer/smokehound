"""HTML report generation using Plotly."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .db import Database


def _ts_to_dt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds / 60:.1f}m"
    else:
        return f"{seconds / 3600:.1f}h"


def generate_report(
    db: Database,
    since: float | None = None,
    until: float | None = None,
    output_path: Path | None = None,
) -> Path:
    """Generate a self-contained HTML report."""
    now = time.time()
    if until is None:
        until = now
    if since is None:
        since = until - 24 * 3600

    where = f"ts >= {since} AND ts <= {until}"
    data = _collect_data(db, where, since, until)
    html = _render_html(data, since, until)

    if output_path is None:
        ts_str = datetime.fromtimestamp(since).strftime("%Y%m%d_%H%M")
        output_path = db.path.parent / "reports" / f"report_{ts_str}.html"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def _collect_data(db: Database, where: str, since: float, until: float) -> dict[str, Any]:
    """Fetch all data needed for the report."""
    data: dict[str, Any] = {}

    # Ping data
    rows = db.fetchall(
        f"SELECT ts, target, rtt_avg_ms, rtt_min_ms, rtt_max_ms, jitter_ms, loss_pct "
        f"FROM ping_results WHERE {where} ORDER BY ts"
    )
    data["ping"] = [dict(r) for r in rows]

    # DNS data
    rows = db.fetchall(
        f"SELECT ts, domain, resolver, resolve_ms, status FROM dns_results WHERE {where} ORDER BY ts"
    )
    data["dns"] = [dict(r) for r in rows]

    # HTTP data
    rows = db.fetchall(
        f"SELECT ts, target, status_code, dns_ms, connect_ms, tls_ms, ttfb_ms, total_ms "
        f"FROM http_results WHERE {where} ORDER BY ts"
    )
    data["http"] = [dict(r) for r in rows]

    # WiFi data
    rows = db.fetchall(
        f"SELECT ts, ssid, rssi_dbm, noise_dbm, channel, link_speed_mbps "
        f"FROM wifi_results WHERE {where} AND rssi_dbm IS NOT NULL ORDER BY ts"
    )
    data["wifi"] = [dict(r) for r in rows]

    # Speedtest data
    rows = db.fetchall(
        f"SELECT ts, download_mbps, upload_mbps, ping_ms FROM speedtest_results "
        f"WHERE {where.replace('ts', 'ts')} AND download_mbps IS NOT NULL ORDER BY ts"
    )
    data["speedtest"] = [dict(r) for r in rows]

    # Outage events
    rows = db.fetchall(
        f"SELECT started_at, ended_at, duration_s, trigger, max_loss_pct "
        f"FROM outage_events WHERE started_at >= {since} AND started_at <= {until} ORDER BY started_at"
    )
    data["outages"] = [dict(r) for r in rows]

    # Traceroute changes
    rows = db.fetchall(
        f"SELECT ts, hop_count, path_hash, changed FROM traceroute_results WHERE {where} ORDER BY ts"
    )
    data["traceroute"] = [dict(r) for r in rows]

    # System events
    rows = db.fetchall(f"SELECT ts, kind, detail FROM system_events WHERE {where} ORDER BY ts")
    data["events"] = [dict(r) for r in rows]

    # Summary stats
    data["summary"] = _compute_summary(data, since, until)

    return data


def _compute_summary(data: dict, since: float, until: float) -> dict:
    summary: dict[str, Any] = {}
    span = until - since

    # Uptime: time not in outage
    outage_total = sum(o.get("duration_s") or 0 for o in data["outages"] if o.get("duration_s"))
    summary["uptime_pct"] = max(0.0, (1 - outage_total / max(span, 1)) * 100)
    summary["total_outages"] = len(data["outages"])
    summary["outage_total_s"] = outage_total

    # Avg latency
    rtts = [r["rtt_avg_ms"] for r in data["ping"] if r.get("rtt_avg_ms") is not None]
    summary["avg_rtt_ms"] = sum(rtts) / len(rtts) if rtts else None

    # p95 latency
    if rtts:
        rtts_sorted = sorted(rtts)
        idx = int(len(rtts_sorted) * 0.95)
        summary["p95_rtt_ms"] = rtts_sorted[min(idx, len(rtts_sorted) - 1)]
    else:
        summary["p95_rtt_ms"] = None

    # Bandwidth
    dl = [r["download_mbps"] for r in data["speedtest"] if r.get("download_mbps")]
    summary["avg_download_mbps"] = sum(dl) / len(dl) if dl else None

    # DNS success rate
    dns_total = len(data["dns"])
    dns_ok = sum(1 for r in data["dns"] if r.get("status") == "ok")
    summary["dns_success_pct"] = (dns_ok / dns_total * 100) if dns_total > 0 else None

    return summary


def _render_html(data: dict, since: float, until: float) -> str:
    """Generate the full HTML report."""
    from_str = _ts_to_dt(since)
    to_str = _ts_to_dt(until)
    generated = _ts_to_dt(time.time())
    s = data["summary"]

    # Pre-compute chart data
    charts_json = json.dumps(data, default=str)

    uptime_pct = f"{s['uptime_pct']:.1f}%" if s.get("uptime_pct") is not None else "N/A"
    avg_rtt = f"{s['avg_rtt_ms']:.1f} ms" if s.get("avg_rtt_ms") is not None else "N/A"
    p95_rtt = f"{s['p95_rtt_ms']:.1f} ms" if s.get("p95_rtt_ms") is not None else "N/A"
    total_outages = s.get("total_outages", 0)
    avg_dl = f"{s['avg_download_mbps']:.1f} Mbps" if s.get("avg_download_mbps") else "N/A"
    dns_ok = f"{s['dns_success_pct']:.1f}%" if s.get("dns_success_pct") is not None else "N/A"

    outage_rows = ""
    for o in data["outages"]:
        dur = _fmt_duration(o.get("duration_s") or 0)
        started = _ts_to_dt(o["started_at"])
        ended = _ts_to_dt(o["ended_at"]) if o.get("ended_at") else "ongoing"
        loss = f"{o.get('max_loss_pct', 0):.0f}%"
        outage_rows += f"<tr><td>{started}</td><td>{ended}</td><td>{dur}</td><td>{loss}</td><td>{o.get('trigger', '')}</td></tr>\n"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SmokеHound Network Report — {from_str} to {to_str}</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
  :root {{
    --bg: #0d1117; --bg2: #161b22; --bg3: #21262d;
    --fg: #e6edf3; --fg2: #8b949e; --accent: #58a6ff;
    --green: #3fb950; --yellow: #d29922; --red: #f85149;
    --border: #30363d;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--fg); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 14px; line-height: 1.6; }}
  .container {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}
  h1 {{ font-size: 24px; font-weight: 600; color: var(--accent); margin-bottom: 4px; }}
  h2 {{ font-size: 16px; font-weight: 600; color: var(--fg); margin: 24px 0 12px; padding-bottom: 8px; border-bottom: 1px solid var(--border); }}
  .subtitle {{ color: var(--fg2); font-size: 13px; margin-bottom: 24px; }}
  .summary-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 28px; }}
  .card {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }}
  .card-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: .08em; color: var(--fg2); margin-bottom: 4px; }}
  .card-value {{ font-size: 22px; font-weight: 700; }}
  .card-value.good {{ color: var(--green); }}
  .card-value.warn {{ color: var(--yellow); }}
  .card-value.bad {{ color: var(--red); }}
  .chart-wrap {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 16px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  thead tr {{ background: var(--bg3); }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border); }}
  th {{ font-weight: 600; color: var(--fg2); font-size: 11px; text-transform: uppercase; letter-spacing: .06em; }}
  tr:last-child td {{ border-bottom: none; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }}
  .badge-red {{ background: rgba(248,81,73,.2); color: var(--red); }}
  footer {{ margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--border); color: var(--fg2); font-size: 12px; }}
</style>
</head>
<body>
<div class="container">
  <h1>SmokеHound Network Report</h1>
  <p class="subtitle">{from_str} &rarr; {to_str} &nbsp;&bull;&nbsp; Generated {generated}</p>

  <div class="summary-cards">
    <div class="card">
      <div class="card-label">Uptime</div>
      <div class="card-value {"good" if s.get("uptime_pct", 0) >= 99 else "warn" if s.get("uptime_pct", 0) >= 95 else "bad"}">{uptime_pct}</div>
    </div>
    <div class="card">
      <div class="card-label">Avg Latency</div>
      <div class="card-value">{avg_rtt}</div>
    </div>
    <div class="card">
      <div class="card-label">p95 Latency</div>
      <div class="card-value">{p95_rtt}</div>
    </div>
    <div class="card">
      <div class="card-label">Total Outages</div>
      <div class="card-value {"bad" if total_outages > 0 else "good"}">{total_outages}</div>
    </div>
    <div class="card">
      <div class="card-label">Avg Download</div>
      <div class="card-value">{avg_dl}</div>
    </div>
    <div class="card">
      <div class="card-label">DNS Success</div>
      <div class="card-value {"good" if (s.get("dns_success_pct") or 0) >= 99 else "warn"}">{dns_ok}</div>
    </div>
  </div>

  <div id="chart-timeline" class="chart-wrap"></div>
  <div id="chart-ping" class="chart-wrap"></div>
  <div id="chart-loss" class="chart-wrap"></div>
  <div id="chart-jitter" class="chart-wrap"></div>
  <div id="chart-dns" class="chart-wrap"></div>
  <div id="chart-http" class="chart-wrap"></div>
  <div id="chart-wifi" class="chart-wrap"></div>
  <div id="chart-speedtest" class="chart-wrap"></div>

  <h2>Outage Log</h2>
  <div class="chart-wrap">
  {'<p style="color:var(--green);padding:8px">No outages detected in this period.</p>' if not data["outages"] else f"<table><thead><tr><th>Started</th><th>Ended</th><th>Duration</th><th>Max Loss</th><th>Trigger</th></tr></thead><tbody>{outage_rows}</tbody></table>"}
  </div>

  <footer>
    Generated by <strong>SmokеHound v0.1.0</strong>
  </footer>
</div>

<script>
const RAW = {charts_json};
const LAYOUT_BASE = {{
  paper_bgcolor: '#161b22',
  plot_bgcolor: '#0d1117',
  font: {{ color: '#e6edf3', size: 12 }},
  height: 360,
  margin: {{ l: 60, r: 20, t: 44, b: 50 }},
  xaxis: {{ gridcolor: '#21262d', zerolinecolor: '#30363d' }},
  yaxis: {{ gridcolor: '#21262d', zerolinecolor: '#30363d' }},
  legend: {{ bgcolor: 'rgba(22,27,34,0.8)', bordercolor: '#30363d', borderwidth: 1 }},
  hovermode: 'x unified',
  autosize: true,
}};
const CONFIG = {{ responsive: true, displayModeBar: true, modeBarButtonsToRemove: ['lasso2d','select2d'] }};

function ts(t) {{ return new Date(t * 1000).toISOString(); }}

// --- Timeline overview ---
(function() {{
  const outages = RAW.outages;
  const since = {since};
  const until = {until};
  const shapes = [];
  outages.forEach(o => {{
    shapes.push({{
      type: 'rect', xref: 'x', yref: 'paper',
      x0: ts(o.started_at), x1: ts(o.ended_at || until),
      y0: 0, y1: 1,
      fillcolor: 'rgba(248,81,73,0.3)', line: {{ width: 0 }}
    }});
  }});
  const trace = {{
    x: [ts(since), ts(until)], y: [1, 1],
    type: 'scatter', mode: 'lines',
    line: {{ color: '#3fb950', width: 4 }},
    name: 'Network up',
    fill: 'tozeroy', fillcolor: 'rgba(63,185,80,0.1)',
  }};
  Plotly.newPlot('chart-timeline', [trace], {{
    ...LAYOUT_BASE,
    title: {{ text: 'Network Status Timeline', font: {{ size: 14 }} }},
    shapes,
    yaxis: {{ ...LAYOUT_BASE.yaxis, showticklabels: false, range: [0, 1.1] }},
    height: 120,
    margin: {{ l: 60, r: 20, t: 44, b: 30 }},
  }}, CONFIG);
}})();

// --- Ping latency ---
(function() {{
  const rows = RAW.ping;
  const targets = [...new Set(rows.map(r => r.target))];
  const COLORS = ['#58a6ff','#3fb950','#d29922','#f85149','#a371f7'];
  const traces = [];
  targets.forEach((tgt, i) => {{
    const sub = rows.filter(r => r.target === tgt && r.rtt_avg_ms != null);
    const x = sub.map(r => ts(r.ts));
    traces.push({{
      x, y: sub.map(r => r.rtt_max_ms),
      type: 'scatter', mode: 'lines', name: `${{tgt}} max`,
      line: {{ color: COLORS[i % COLORS.length], width: 0 }},
      fill: 'tonexty', fillcolor: COLORS[i % COLORS.length].replace(')', ',0.1)').replace('rgb', 'rgba'),
      showlegend: false,
    }});
    traces.push({{
      x, y: sub.map(r => r.rtt_avg_ms),
      type: 'scatter', mode: 'lines', name: tgt,
      line: {{ color: COLORS[i % COLORS.length], width: 2 }},
      fill: 'tonexty', fillcolor: COLORS[i % COLORS.length].replace(')', ',0.1)').replace('rgb', 'rgba'),
    }});
    traces.push({{
      x, y: sub.map(r => r.rtt_min_ms),
      type: 'scatter', mode: 'lines', name: `${{tgt}} min`,
      line: {{ color: COLORS[i % COLORS.length], width: 0 }},
      showlegend: false,
    }});
  }});
  Plotly.newPlot('chart-ping', traces, {{
    ...LAYOUT_BASE,
    title: {{ text: 'Ping RTT (ms) — min/avg/max bands', font: {{ size: 14 }} }},
    yaxis: {{ ...LAYOUT_BASE.yaxis, title: 'ms' }},
  }}, CONFIG);
}})();

// --- Packet loss ---
(function() {{
  const rows = RAW.ping;
  const targets = [...new Set(rows.map(r => r.target))];
  const COLORS = ['#58a6ff','#3fb950','#d29922','#f85149','#a371f7'];
  const traces = targets.map((tgt, i) => {{
    const sub = rows.filter(r => r.target === tgt);
    return {{
      x: sub.map(r => ts(r.ts)),
      y: sub.map(r => r.loss_pct),
      type: 'scatter', mode: 'lines', name: tgt,
      line: {{ color: COLORS[i % COLORS.length], width: 1.5 }},
      fill: 'tozeroy', fillcolor: 'rgba(248,81,73,0.05)',
    }};
  }});
  Plotly.newPlot('chart-loss', traces, {{
    ...LAYOUT_BASE,
    title: {{ text: 'Packet Loss %', font: {{ size: 14 }} }},
    yaxis: {{ ...LAYOUT_BASE.yaxis, title: '%', range: [0, 105] }},
  }}, CONFIG);
}})();

// --- Jitter ---
(function() {{
  const rows = RAW.ping;
  const targets = [...new Set(rows.map(r => r.target))];
  const COLORS = ['#58a6ff','#3fb950','#d29922','#f85149','#a371f7'];
  const traces = targets.map((tgt, i) => {{
    const sub = rows.filter(r => r.target === tgt && r.jitter_ms != null);
    return {{
      x: sub.map(r => ts(r.ts)),
      y: sub.map(r => r.jitter_ms),
      type: 'scatter', mode: 'lines', name: tgt,
      line: {{ color: COLORS[i % COLORS.length], width: 1.5 }},
    }};
  }});
  Plotly.newPlot('chart-jitter', traces, {{
    ...LAYOUT_BASE,
    title: {{ text: 'Jitter (RTT std dev, ms)', font: {{ size: 14 }} }},
    yaxis: {{ ...LAYOUT_BASE.yaxis, title: 'ms' }},
  }}, CONFIG);
}})();

// --- DNS performance ---
(function() {{
  const rows = RAW.dns;
  const resolvers = [...new Set(rows.map(r => r.resolver))];
  const COLORS = ['#58a6ff','#3fb950','#d29922','#f85149'];
  const traces = resolvers.map((res, i) => {{
    const sub = rows.filter(r => r.resolver === res && r.resolve_ms != null);
    return {{
      x: sub.map(r => ts(r.ts)),
      y: sub.map(r => r.resolve_ms),
      type: 'scatter', mode: 'markers', name: res,
      marker: {{ color: COLORS[i % COLORS.length], size: 4, opacity: 0.7 }},
    }};
  }});
  const failures = rows.filter(r => r.status !== 'ok');
  if (failures.length) {{
    traces.push({{
      x: failures.map(r => ts(r.ts)),
      y: failures.map(() => 0),
      type: 'scatter', mode: 'markers', name: 'failures',
      marker: {{ color: '#f85149', size: 8, symbol: 'x' }},
    }});
  }}
  Plotly.newPlot('chart-dns', traces, {{
    ...LAYOUT_BASE,
    title: {{ text: 'DNS Resolution Time (ms) per Resolver', font: {{ size: 14 }} }},
    yaxis: {{ ...LAYOUT_BASE.yaxis, title: 'ms' }},
  }}, CONFIG);
}})();

// --- HTTP latency breakdown ---
(function() {{
  const rows = RAW.http.filter(r => r.total_ms != null);
  const targets = [...new Set(rows.map(r => r.target))];
  const phases = [
    {{ key: 'dns_ms', name: 'DNS', color: '#58a6ff' }},
    {{ key: 'connect_ms', name: 'Connect', color: '#3fb950' }},
    {{ key: 'tls_ms', name: 'TLS', color: '#d29922' }},
    {{ key: 'ttfb_ms', name: 'TTFB', color: '#a371f7' }},
  ];
  // Use first target only for breakdown clarity
  const tgt = targets[0];
  if (!tgt) return;
  const sub = rows.filter(r => r.target === tgt);
  const traces = phases.map(ph => ({{
    x: sub.map(r => ts(r.ts)),
    y: sub.map(r => r[ph.key] || 0),
    type: 'bar', name: ph.name,
    marker: {{ color: ph.color }},
  }}));
  Plotly.newPlot('chart-http', traces, {{
    ...LAYOUT_BASE,
    barmode: 'stack',
    title: {{ text: `HTTP Latency Breakdown — ${{tgt}}`, font: {{ size: 14 }} }},
    yaxis: {{ ...LAYOUT_BASE.yaxis, title: 'ms' }},
  }}, CONFIG);
}})();

// --- WiFi signal strength ---
(function() {{
  const rows = RAW.wifi;
  if (!rows.length) {{
    document.getElementById('chart-wifi').innerHTML = '<p style="color:var(--fg2);padding:16px">No WiFi data collected.</p>';
    return;
  }}
  const traces = [{{
    x: rows.map(r => ts(r.ts)),
    y: rows.map(r => r.rssi_dbm),
    type: 'scatter', mode: 'lines', name: 'RSSI (dBm)',
    line: {{ color: '#58a6ff', width: 1.5 }},
    fill: 'tozeroy', fillcolor: 'rgba(88,166,255,0.1)',
  }}];
  if (rows.some(r => r.noise_dbm != null)) {{
    traces.push({{
      x: rows.map(r => ts(r.ts)),
      y: rows.map(r => r.noise_dbm),
      type: 'scatter', mode: 'lines', name: 'Noise (dBm)',
      line: {{ color: '#8b949e', width: 1, dash: 'dot' }},
    }});
  }}
  Plotly.newPlot('chart-wifi', traces, {{
    ...LAYOUT_BASE,
    title: {{ text: 'WiFi Signal Strength (dBm)', font: {{ size: 14 }} }},
    yaxis: {{ ...LAYOUT_BASE.yaxis, title: 'dBm', range: [-100, 0] }},
  }}, CONFIG);
}})();

// --- Bandwidth ---
(function() {{
  const rows = RAW.speedtest;
  if (!rows.length) {{
    document.getElementById('chart-speedtest').innerHTML = '<p style="color:var(--fg2);padding:16px">No bandwidth data collected yet.</p>';
    return;
  }}
  const traces = [
    {{
      x: rows.map(r => ts(r.ts)),
      y: rows.map(r => r.download_mbps),
      type: 'scatter', mode: 'markers+lines', name: 'Download',
      marker: {{ color: '#3fb950', size: 8 }},
      line: {{ color: '#3fb950', width: 1.5 }},
    }},
    {{
      x: rows.map(r => ts(r.ts)),
      y: rows.map(r => r.upload_mbps),
      type: 'scatter', mode: 'markers+lines', name: 'Upload',
      marker: {{ color: '#58a6ff', size: 8 }},
      line: {{ color: '#58a6ff', width: 1.5 }},
    }},
  ];
  Plotly.newPlot('chart-speedtest', traces, {{
    ...LAYOUT_BASE,
    title: {{ text: 'Bandwidth Spot-check (Mbps)', font: {{ size: 14 }} }},
    yaxis: {{ ...LAYOUT_BASE.yaxis, title: 'Mbps' }},
  }}, CONFIG);
}})();
</script>
</body>
</html>
"""
    return html
