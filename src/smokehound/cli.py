"""SmokеHound CLI — click-based command interface."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .config import DEFAULT_CONFIG_PATH, ensure_data_dirs, load_config, write_default_config
from .db import Database

console = Console()
err_console = Console(stderr=True)


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
@click.version_option(__version__, prog_name="smokehound")
def main() -> None:
    """SmokеHound — network diagnostics collector and visualizer."""


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------

@main.command()
@click.option("-d", "--daemon", is_flag=True, help="Run as background daemon.")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
@click.option(
    "-c", "--config", "config_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to config file.",
)
@click.option("--interval", type=int, default=None, help="Override measurement interval (seconds).")
def start(daemon: bool, verbose: bool, config_path: Path | None, interval: int | None) -> None:
    """Start collecting network diagnostics."""
    _setup_logging(verbose)
    cfg = load_config(config_path)

    if interval:
        cfg.general.interval_seconds = interval

    if not config_path and not DEFAULT_CONFIG_PATH.exists():
        write_default_config(DEFAULT_CONFIG_PATH)
        console.print(f"[dim]Created default config at {DEFAULT_CONFIG_PATH}[/dim]")

    ensure_data_dirs(cfg)

    if daemon:
        _start_daemon(cfg)
    else:
        _start_foreground(cfg, verbose)


def _start_foreground(cfg, verbose: bool) -> None:
    from .engine import Engine

    engine = Engine(cfg)
    live_table: list = []  # mutable for closure

    def on_measurement(kind: str, data):
        if kind == "ping" and isinstance(data, list):
            live_table.clear()
            live_table.extend(data)

    engine.add_measurement_callback(on_measurement)

    console.print(
        Panel.fit(
            f"[bold cyan]SmokеHound[/bold cyan] v{__version__}\n"
            f"[dim]DB: {cfg.db_path}\n"
            f"Interval: {cfg.general.interval_seconds}s | Press Ctrl+C to stop[/dim]",
            border_style="cyan",
        )
    )

    import contextlib

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(engine.start())
    console.print("\n[green]SmokеHound stopped.[/green]")


def _start_daemon(cfg) -> None:
    pid_file = cfg.pid_file

    if pid_file.exists():
        existing_pid = int(pid_file.read_text().strip())
        try:
            os.kill(existing_pid, 0)
            console.print(f"[red]SmokеHound already running (PID {existing_pid})[/red]")
            sys.exit(1)
        except ProcessLookupError:
            pid_file.unlink()

    pid = os.fork()
    if pid > 0:
        # Parent
        console.print(f"[green]SmokеHound started as daemon (PID {pid})[/green]")
        console.print(f"[dim]DB: {cfg.db_path}[/dim]")
        console.print("[dim]Run 'smokehound stop' to stop.[/dim]")
        sys.exit(0)

    # Child
    os.setsid()
    pid_file.write_text(str(os.getpid()))

    # Redirect stdio
    import contextlib

    devnull = open(os.devnull, "w")  # noqa: SIM115 - intentional: assigned to sys.stdout/stderr
    sys.stdout = devnull
    sys.stderr = devnull

    from .engine import Engine

    engine = Engine(cfg)
    try:
        asyncio.run(engine.start())
    finally:
        with contextlib.suppress(FileNotFoundError):
            pid_file.unlink()


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------

@main.command()
@click.option(
    "-c", "--config", "config_path",
    type=click.Path(path_type=Path), default=None,
)
def stop(config_path: Path | None) -> None:
    """Stop a running background daemon."""
    cfg = load_config(config_path)
    pid_file = cfg.pid_file

    if not pid_file.exists():
        console.print("[yellow]SmokеHound is not running (no PID file found).[/yellow]")
        sys.exit(1)

    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        console.print(f"[green]Sent SIGTERM to SmokеHound (PID {pid})[/green]")
    except ProcessLookupError:
        console.print(f"[yellow]Process {pid} not found. Removing stale PID file.[/yellow]")
        pid_file.unlink()
        sys.exit(1)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@main.command()
@click.option(
    "-c", "--config", "config_path",
    type=click.Path(path_type=Path), default=None,
)
def status(config_path: Path | None) -> None:
    """Show current status, uptime, and measurement counts."""
    cfg = load_config(config_path)

    # Check if running
    pid_file = cfg.pid_file
    running = False
    pid = None
    if pid_file.exists():
        pid = int(pid_file.read_text().strip())
        try:
            os.kill(pid, 0)
            running = True
        except ProcessLookupError:
            pass

    if running:
        console.print(f"[green]● SmokеHound running[/green] (PID {pid})")
    else:
        console.print("[dim]● SmokеHound not running[/dim]")

    if not cfg.db_path.exists():
        console.print("[dim]No database found yet.[/dim]")
        return

    db = Database(cfg.db_path)
    db.connect()

    stats = db.get_stats()
    run = db.get_last_run()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()

    if run:
        started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run["started_at"]))
        table.add_row("Last run started", started)
        if run["ended_at"]:
            ended = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run["ended_at"]))
            table.add_row("Last run ended", ended)

    table.add_row("Ping measurements", str(stats["ping_count"]))
    table.add_row("Outage events", str(stats["outage_count"]))
    table.add_row("DB size", f"{stats['db_size_bytes'] / 1024:.1f} KB")
    table.add_row("DB path", str(cfg.db_path))

    console.print(table)
    db.close()


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

@main.command()
@click.option("--last", "last_window", default=None, help="Window like '4h', '24h', '7d'.")
@click.option("--from", "from_dt", default=None, help="Start datetime (YYYY-MM-DD HH:MM).")
@click.option("--to", "to_dt", default=None, help="End datetime (YYYY-MM-DD HH:MM).")
@click.option("-o", "--output", "output_path", type=click.Path(path_type=Path), default=None)
@click.option("--no-open", is_flag=True, help="Don't open the report in browser.")
@click.option(
    "-c", "--config", "config_path",
    type=click.Path(path_type=Path), default=None,
)
def report(
    last_window: str | None,
    from_dt: str | None,
    to_dt: str | None,
    output_path: Path | None,
    no_open: bool,
    config_path: Path | None,
) -> None:
    """Generate an HTML report and open it in the browser."""
    from .report import generate_report

    cfg = load_config(config_path)

    if not cfg.db_path.exists():
        console.print("[red]No database found. Run 'smokehound start' first.[/red]")
        sys.exit(1)

    now = time.time()
    since: float
    until: float = now

    if from_dt and to_dt:
        from datetime import datetime

        since = datetime.strptime(from_dt, "%Y-%m-%d %H:%M").timestamp()
        until = datetime.strptime(to_dt, "%Y-%m-%d %H:%M").timestamp()
    elif last_window:
        since = now - _parse_window(last_window)
    else:
        since = now - _parse_window(cfg.report.default_window)

    db = Database(cfg.db_path)
    db.connect()

    with console.status("Generating report..."):
        out = generate_report(db, since=since, until=until, output_path=output_path)

    db.close()
    console.print(f"[green]Report saved:[/green] {out}")

    if not no_open:
        import subprocess

        if sys.platform == "darwin":
            subprocess.Popen(["open", str(out)])
        else:
            subprocess.Popen(["xdg-open", str(out)])


def _parse_window(window: str) -> float:
    from .utils import parse_window
    return parse_window(window)


# ---------------------------------------------------------------------------
# tail
# ---------------------------------------------------------------------------

@main.command()
@click.option(
    "-c", "--config", "config_path",
    type=click.Path(path_type=Path), default=None,
)
def tail(config_path: Path | None) -> None:
    """Live tail of measurements (reads from DB, refreshes every 5s)."""
    cfg = load_config(config_path)

    if not cfg.db_path.exists():
        console.print("[red]No database found. Run 'smokehound start' first.[/red]")
        sys.exit(1)

    db = Database(cfg.db_path)
    db.connect()

    def _build_table() -> Table:
        t = Table(title="SmokеHound Live Measurements", border_style="dim")
        t.add_column("Time", style="dim", width=20)
        t.add_column("Target", width=16)
        t.add_column("RTT avg", justify="right", width=10)
        t.add_column("Loss%", justify="right", width=8)
        t.add_column("Jitter", justify="right", width=8)

        rows = db.fetchall(
            "SELECT ts, target, rtt_avg_ms, loss_pct, jitter_ms "
            "FROM ping_results ORDER BY ts DESC LIMIT 30"
        )
        for row in rows:
            ts_str = time.strftime("%H:%M:%S", time.localtime(row["ts"]))
            rtt = f"{row['rtt_avg_ms']:.1f}" if row["rtt_avg_ms"] else "—"
            loss = row["loss_pct"]
            loss_style = "red" if loss > 50 else "yellow" if loss > 0 else "green"
            jitter = f"{row['jitter_ms']:.1f}" if row["jitter_ms"] else "—"
            t.add_row(
                ts_str,
                row["target"],
                rtt,
                Text(f"{loss:.0f}%", style=loss_style),
                jitter,
            )
        return t

    try:
        with Live(console=console, refresh_per_second=0.2) as live:
            while True:
                live.update(_build_table())
                time.sleep(5)
    except KeyboardInterrupt:
        pass

    db.close()


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

@main.command()
@click.option("--last", "last_window", default="24h", show_default=True)
@click.option("-o", "--output", "output_path", type=click.Path(path_type=Path), default=None)
@click.option(
    "-c", "--config", "config_path",
    type=click.Path(path_type=Path), default=None,
)
def export(
    last_window: str, output_path: Path | None, config_path: Path | None
) -> None:
    """Export measurements to CSV."""
    import csv

    cfg = load_config(config_path)

    if not cfg.db_path.exists():
        console.print("[red]No database found.[/red]")
        sys.exit(1)

    since = time.time() - _parse_window(last_window)
    db = Database(cfg.db_path)
    db.connect()

    rows = db.fetchall(
        f"SELECT ts, target, rtt_avg_ms, rtt_min_ms, rtt_max_ms, jitter_ms, loss_pct "
        f"FROM ping_results WHERE ts >= {since} ORDER BY ts"
    )

    if output_path is None:
        ts_str = time.strftime("%Y%m%d_%H%M")
        output_path = Path(f"smokehound_ping_{ts_str}.csv")

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "target", "rtt_avg_ms", "rtt_min_ms", "rtt_max_ms", "jitter_ms", "loss_pct"])
        for row in rows:
            writer.writerow([
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["ts"])),
                row["target"], row["rtt_avg_ms"], row["rtt_min_ms"],
                row["rtt_max_ms"], row["jitter_ms"], row["loss_pct"],
            ])

    db.close()
    console.print(f"[green]Exported {len(rows)} rows to {output_path}[/green]")


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

@main.command()
def doctor() -> None:
    """Check system capabilities and diagnose potential issues."""
    import shutil
    import subprocess

    from .collectors.gateway import detect_gateway
    from .collectors.ping import can_use_icmp

    table = Table(title="SmokеHound System Check", border_style="cyan")
    table.add_column("Check", width=30)
    table.add_column("Status", width=10)
    table.add_column("Details")

    def ok(label: str, detail: str = "") -> None:
        table.add_row(label, Text("OK", style="green"), detail)

    def warn(label: str, detail: str = "") -> None:
        table.add_row(label, Text("WARN", style="yellow"), detail)

    def fail(label: str, detail: str = "") -> None:
        table.add_row(label, Text("FAIL", style="red"), detail)

    # Python version
    v = sys.version_info
    if v >= (3, 10):
        ok("Python version", f"{v.major}.{v.minor}.{v.micro}")
    else:
        fail("Python version", f"{v.major}.{v.minor} — requires 3.10+")

    # ICMP ping
    if can_use_icmp():
        ok("ICMP ping", "ping works without sudo")
    else:
        warn("ICMP ping", "using TCP fallback — run with sudo for ICMP")

    # ping binary
    ping_path = shutil.which("ping")
    if ping_path:
        ok("ping binary", ping_path)
    else:
        fail("ping binary", "not found in PATH")

    # traceroute
    tr = shutil.which("traceroute") or shutil.which("tracepath")
    if tr:
        ok("traceroute", tr)
    else:
        warn("traceroute", "not found — traceroute collection disabled")

    # speedtest-cli
    st = shutil.which("speedtest-cli")
    if st:
        ok("speedtest-cli", st)
    else:
        warn("speedtest-cli", "not found — using Cloudflare fallback")

    # Gateway detection
    gw = detect_gateway()
    if gw:
        ok("Gateway detection", gw)
    else:
        warn("Gateway detection", "could not detect default gateway")

    # WiFi tool
    if sys.platform == "darwin":
        airport = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
        if os.path.exists(airport):
            ok("WiFi (airport)", airport)
        else:
            warn("WiFi (airport)", "not found — WiFi metrics unavailable")
    else:
        iw = shutil.which("iw") or shutil.which("iwconfig")
        if iw:
            ok("WiFi tool", iw)
        else:
            warn("WiFi tool", "iw/iwconfig not found — WiFi metrics unavailable")

    # Writeable data dir
    cfg = load_config()
    try:
        ensure_data_dirs(cfg)
        (cfg.data_dir / ".test_write").write_text("ok")
        (cfg.data_dir / ".test_write").unlink()
        ok("Data directory", str(cfg.data_dir))
    except Exception as e:
        fail("Data directory", str(e))

    # DNS resolution test
    try:
        import socket

        socket.getaddrinfo("google.com", 80, family=socket.AF_INET)
        ok("DNS (system)", "google.com resolved")
    except Exception as e:
        fail("DNS (system)", str(e))

    # HTTP connectivity
    try:
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "5",
             "https://www.google.com/generate_204"],
            capture_output=True, text=True, timeout=10,
        )
        code = result.stdout.strip()
        if code in ("204", "200"):
            ok("HTTP connectivity", f"HTTP {code}")
        else:
            warn("HTTP connectivity", f"HTTP {code}")
    except Exception as e:
        warn("HTTP connectivity", f"curl failed: {e}")

    console.print(table)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

@main.command("config")
@click.option(
    "-c", "--config", "config_path",
    type=click.Path(path_type=Path), default=None,
)
def show_config(config_path: Path | None) -> None:
    """Print current configuration."""
    import dataclasses

    cfg = load_config(config_path)
    config_file = config_path or DEFAULT_CONFIG_PATH
    console.print(f"[dim]Config file:[/dim] {config_file}")
    console.print(f"[dim]Exists:[/dim] {config_file.exists()}")
    console.print()

    for section_name, section in dataclasses.asdict(cfg).items():
        console.print(f"[bold cyan][{section_name}][/bold cyan]")
        if isinstance(section, dict):
            for k, v in section.items():
                console.print(f"  {k} = {v!r}")
        console.print()


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

@main.command()
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@click.option(
    "-c", "--config", "config_path",
    type=click.Path(path_type=Path), default=None,
)
def reset(yes: bool, config_path: Path | None) -> None:
    """Wipe the database and start fresh."""
    cfg = load_config(config_path)

    if not yes:
        click.confirm(
            f"[bold red]Delete {cfg.db_path}?[/bold red] This cannot be undone.",
            abort=True,
        )

    if cfg.db_path.exists():
        cfg.db_path.unlink()
        console.print(f"[green]Deleted {cfg.db_path}[/green]")
    else:
        console.print("[dim]No database found.[/dim]")
