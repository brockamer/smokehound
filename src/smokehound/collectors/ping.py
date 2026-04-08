"""ICMP and TCP ping collector."""

from __future__ import annotations

import asyncio
import re
import statistics
import sys
import time
from typing import Any


async def ping_target(
    target: str,
    count: int = 5,
    timeout: int = 5,
    use_icmp: bool = True,
) -> dict[str, Any]:
    """Ping a target and return RTT stats."""
    ts = time.time()
    if use_icmp:
        return await _icmp_ping(target, count, timeout, ts)
    return await _tcp_ping(target, 80, count, timeout, ts)


async def _icmp_ping(target: str, count: int, timeout: int, ts: float) -> dict[str, Any]:
    """Use system ping command."""
    result: dict[str, Any] = {
        "ts": ts,
        "target": target,
        "rtt_min_ms": None,
        "rtt_avg_ms": None,
        "rtt_max_ms": None,
        "jitter_ms": None,
        "loss_pct": 100.0,
        "packets_sent": count,
        "packets_recv": 0,
        "error": None,
    }

    if sys.platform == "darwin":
        cmd = ["ping", "-c", str(count), "-W", str(timeout * 1000), "-t", str(timeout + 2), target]
    else:
        cmd = ["ping", "-c", str(count), "-W", str(timeout), target]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        total_timeout = timeout * count + 5
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=total_timeout)
        text = stdout.decode(errors="replace")
        _parse_ping_output(text, result, count)
    except asyncio.TimeoutError:
        result["error"] = "ping timeout"
    except FileNotFoundError:
        result["error"] = "ping not found"
    except PermissionError:
        result["error"] = "permission denied"
    except Exception as e:
        result["error"] = str(e)

    return result


def _parse_ping_output(text: str, result: dict[str, Any], count: int) -> None:
    """Parse ping output, supporting macOS and Linux formats."""
    # Packet loss
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*packet loss", text)
    if m:
        result["loss_pct"] = float(m.group(1))
        result["packets_recv"] = round(count * (1 - result["loss_pct"] / 100))

    # RTT stats: min/avg/max/stddev (macOS) or min/avg/max/mdev (Linux)
    m = re.search(
        r"(?:round-trip|rtt)\s+min/avg/max/(?:std)?(?:dev|mdev)\s*=\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)",
        text,
    )
    if m:
        result["rtt_min_ms"] = float(m.group(1))
        result["rtt_avg_ms"] = float(m.group(2))
        result["rtt_max_ms"] = float(m.group(3))
        result["jitter_ms"] = float(m.group(4))
    else:
        # Fall back to parsing individual RTT lines
        rtts = [float(x) for x in re.findall(r"time[=<]([\d.]+)\s*ms", text)]
        if rtts:
            result["rtt_min_ms"] = min(rtts)
            result["rtt_avg_ms"] = statistics.mean(rtts)
            result["rtt_max_ms"] = max(rtts)
            result["jitter_ms"] = statistics.stdev(rtts) if len(rtts) > 1 else 0.0
            result["packets_recv"] = len(rtts)
            result["loss_pct"] = max(0.0, (1 - len(rtts) / result["packets_sent"]) * 100)


async def _tcp_ping(
    target: str,
    port: int,
    count: int,
    timeout: int,
    ts: float,
) -> dict[str, Any]:
    """TCP connect-based ping fallback."""
    result: dict[str, Any] = {
        "ts": ts,
        "target": target,
        "rtt_min_ms": None,
        "rtt_avg_ms": None,
        "rtt_max_ms": None,
        "jitter_ms": None,
        "loss_pct": 100.0,
        "packets_sent": count,
        "packets_recv": 0,
        "error": None,
    }
    rtts: list[float] = []

    for _ in range(count):
        t0 = time.perf_counter()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(target, port), timeout=timeout
            )
            rtt = (time.perf_counter() - t0) * 1000
            writer.close()
            await writer.wait_closed()
            rtts.append(rtt)
        except Exception:
            pass
        await asyncio.sleep(0.2)

    recv = len(rtts)
    result["packets_recv"] = recv
    result["loss_pct"] = max(0.0, (1 - recv / count) * 100)

    if rtts:
        result["rtt_min_ms"] = min(rtts)
        result["rtt_avg_ms"] = statistics.mean(rtts)
        result["rtt_max_ms"] = max(rtts)
        result["jitter_ms"] = statistics.stdev(rtts) if len(rtts) > 1 else 0.0

    return result


def can_use_icmp() -> bool:
    """Check if we can run ICMP ping without sudo."""
    import shutil
    import subprocess

    if not shutil.which("ping"):
        return False
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "1000" if sys.platform == "darwin" else "1", "127.0.0.1"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False
