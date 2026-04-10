"""Traceroute collector."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import sys
import time
from typing import Any


async def run_traceroute(target: str, max_hops: int = 30) -> dict[str, Any]:
    """Run traceroute and return hop information."""
    ts = time.time()
    result: dict[str, Any] = {
        "ts": ts,
        "target": target,
        "hop_count": None,
        "path_hash": None,
        "hops_json": None,
        "changed": 0,
        "error": None,
    }

    hops = await _do_traceroute(target, max_hops)
    if hops is None:
        result["error"] = "traceroute failed or not available"
        return result

    result["hop_count"] = len(hops)
    result["hops_json"] = json.dumps(hops)

    # Hash the path by hop IPs (ignore latency for change detection)
    hop_ips = [h.get("ip", "*") for h in hops]
    path_str = "|".join(hop_ips)
    result["path_hash"] = hashlib.md5(path_str.encode()).hexdigest()[:12]

    return result


async def _do_traceroute(target: str, max_hops: int) -> list[dict] | None:
    if sys.platform == "darwin":
        cmd = ["traceroute", "-n", "-m", str(max_hops), "-w", "2", target]
    else:
        # Prefer traceroute over tracepath
        if shutil.which("traceroute"):
            cmd = ["traceroute", "-n", "-m", str(max_hops), "-w", "2", target]
        elif shutil.which("tracepath"):
            cmd = ["tracepath", "-n", "-m", str(max_hops), target]
        else:
            return None

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=max_hops * 3 + 10)
        text = stdout.decode(errors="replace")
        return _parse_traceroute(text)
    except asyncio.TimeoutError:
        return None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _parse_traceroute(text: str) -> list[dict]:
    """Parse traceroute output into a list of hop dicts."""
    hops = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Match hop number at start
        m = re.match(r"^(\d+)\s+", line)
        if not m:
            continue
        hop_num = int(m.group(1))
        remainder = line[m.end() :]

        # Check for * * * (no response)
        if re.match(r"^[\*\s]+$", remainder):
            hops.append({"hop": hop_num, "ip": "*", "rtts_ms": []})
            continue

        # Extract IP
        ip_match = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", remainder)
        ip = ip_match.group(1) if ip_match else "*"

        # Extract RTTs
        rtts = [float(x) for x in re.findall(r"([\d.]+)\s*ms", remainder)]

        hops.append(
            {
                "hop": hop_num,
                "ip": ip,
                "rtts_ms": rtts,
                "avg_ms": sum(rtts) / len(rtts) if rtts else None,
            }
        )

    return hops
