"""Main collection engine — coordinates all collectors."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from typing import Any

from . import __version__
from .collectors.dns import collect_dns_round
from .collectors.gateway import collect_wifi, detect_gateway
from .collectors.http import collect_http_round
from .collectors.ping import can_use_icmp, ping_target
from .collectors.speedtest import run_speedtest
from .collectors.traceroute import run_traceroute
from .config import Config, ensure_data_dirs
from .db import Database
from .outage import OutageState

logger = logging.getLogger(__name__)

_SLEEP_WAKE_GAP_SECONDS = 300  # 5 minutes — if gap > this, assume sleep


class Engine:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.db = Database(config.db_path)
        self.run_id: int = 0
        self._stop_event = asyncio.Event()
        self._use_icmp = True
        self._gateway: str | None = None
        self._last_ts: float = 0.0
        self._last_traceroute: float = 0.0
        self._last_speedtest: float = 0.0
        self._outage: OutageState | None = None
        self._cycle_count: int = 0
        self._on_measurement: list = []  # callbacks for `tail`

    def add_measurement_callback(self, cb) -> None:
        self._on_measurement.append(cb)

    def _notify(self, kind: str, data: Any) -> None:
        import contextlib

        for cb in self._on_measurement:
            with contextlib.suppress(Exception):
                cb(kind, data)

    async def start(self) -> None:
        ensure_data_dirs(self.config)
        self.db.connect()

        self._use_icmp = can_use_icmp()
        if not self._use_icmp:
            logger.warning("ICMP ping not available, using TCP fallback")

        self._gateway = detect_gateway()
        if self._gateway:
            logger.info("Detected gateway: %s", self._gateway)

        self.run_id = self.db.start_run(os.getpid(), __version__)
        self.db.log_event(self.run_id, "start", f"gateway={self._gateway}")
        self._outage = OutageState(self.config.outage, self.db, self.run_id)
        self._last_ts = time.time()

        # Install signal handlers
        import contextlib

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with contextlib.suppress(OSError, RuntimeError):
                loop.add_signal_handler(sig, self._stop_event.set)

        logger.info("SmokеHound started (run_id=%d)", self.run_id)
        await self._run_loop()

    async def stop(self) -> None:
        self._stop_event.set()

    async def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                cycle_start = time.time()

                # Sleep/wake detection
                if self._last_ts > 0:
                    gap = cycle_start - self._last_ts
                    if gap > _SLEEP_WAKE_GAP_SECONDS:
                        logger.info("Sleep/wake detected (gap=%.0fs)", gap)
                        self.db.log_event(
                            self.run_id,
                            "sleep_wake",
                            f"gap={gap:.0f}s",
                        )

                self._last_ts = cycle_start
                self._cycle_count += 1

                await self._run_cycle(cycle_start)

                elapsed = time.time() - cycle_start
                sleep_for = max(0.0, self.config.general.interval_seconds - elapsed)
                import contextlib

                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_for)

        finally:
            await self._shutdown()

    async def _run_cycle(self, ts: float) -> None:
        # Build ping targets
        ping_targets = []
        for t in self.config.ping.targets:
            if t == "gateway":
                if self._gateway:
                    ping_targets.append(self._gateway)
            else:
                ping_targets.append(t)

        # Ping (run concurrently per target)
        ping_tasks = [
            ping_target(
                t,
                count=self.config.ping.count,
                timeout=self.config.ping.timeout_seconds,
                use_icmp=self._use_icmp,
            )
            for t in ping_targets
        ]
        ping_results = list(await asyncio.gather(*ping_tasks, return_exceptions=True))
        ping_results = [
            r
            if isinstance(r, dict)
            else {
                "ts": ts,
                "target": "?",
                "loss_pct": 100.0,
                "packets_sent": 0,
                "packets_recv": 0,
                "rtt_min_ms": None,
                "rtt_avg_ms": None,
                "rtt_max_ms": None,
                "jitter_ms": None,
                "error": str(r),
            }
            for r in ping_results
        ]
        for r in ping_results:
            self.db.insert_ping(self.run_id, r)
        self._notify("ping", ping_results)

        # Outage detection
        if self._outage:
            self._outage.update(ping_results)

        # DNS (every cycle)
        dns_results = await collect_dns_round(
            self.config.dns.domains,
            self.config.dns.resolvers,
            self.config.dns.timeout_seconds,
        )
        for r in dns_results:
            self.db.insert_dns(self.run_id, r)
        self._notify("dns", dns_results)

        # HTTP (every cycle)
        http_results = await collect_http_round(
            self.config.http.targets,
            self.config.http.timeout_seconds,
        )
        for r in http_results:
            self.db.insert_http(self.run_id, r)
        self._notify("http", http_results)

        # WiFi (every cycle)
        wifi = await collect_wifi()
        self.db.insert_wifi(self.run_id, wifi)
        self._notify("wifi", wifi)

        # Traceroute (periodic)
        now = time.time()
        traceroute_interval = self.config.traceroute.interval_minutes * 60
        if now - self._last_traceroute >= traceroute_interval:
            self._last_traceroute = now
            tr = await run_traceroute(self.config.traceroute.target)
            last_hash = self.db.get_last_traceroute_hash(self.config.traceroute.target)
            if last_hash and tr.get("path_hash") and tr["path_hash"] != last_hash:
                tr["changed"] = 1
                logger.info("Traceroute path changed!")
                self.db.log_event(
                    self.run_id, "traceroute_change", f"old={last_hash} new={tr['path_hash']}"
                )
            self.db.insert_traceroute(self.run_id, tr)
            self._notify("traceroute", tr)

        # Speedtest (periodic)
        if self.config.speedtest.enabled:
            speedtest_interval = self.config.speedtest.interval_minutes * 60
            if now - self._last_speedtest >= speedtest_interval:
                self._last_speedtest = now
                st = await run_speedtest()
                self.db.insert_speedtest(self.run_id, st)
                self._notify("speedtest", st)

        logger.debug("Cycle %d complete (%.2fs)", self._cycle_count, time.time() - ts)

    async def _shutdown(self) -> None:
        logger.info("Shutting down (cycles=%d)...", self._cycle_count)
        if self._outage:
            self._outage.finalize()
        self.db.log_event(self.run_id, "stop", f"cycles={self._cycle_count}")
        self.db.end_run(self.run_id)
        self.db.close()
        logger.info("SmokеHound stopped.")
