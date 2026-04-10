"""Auto-detect default gateway and capture WiFi signal info."""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import sys
import time
from typing import Any


def detect_gateway() -> str | None:
    """Return the default gateway IP address, or None."""
    if sys.platform == "darwin":
        return _gateway_macos()
    return _gateway_linux()


def _gateway_macos() -> str | None:
    try:
        out = subprocess.check_output(
            ["route", "-n", "get", "default"], timeout=5, text=True, stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            m = re.search(r"gateway:\s+(\S+)", line)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def _gateway_linux() -> str | None:
    try:
        out = subprocess.check_output(
            ["ip", "route", "show", "default"], timeout=5, text=True, stderr=subprocess.DEVNULL
        )
        m = re.search(r"default via (\S+)", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    # Fallback: parse /proc/net/route
    try:
        with open("/proc/net/route") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "00000000":
                    # Gateway in hex, little-endian
                    gw_hex = parts[2]
                    gw = ".".join(str(int(gw_hex[i : i + 2], 16)) for i in (6, 4, 2, 0))
                    return gw
    except Exception:
        pass
    return None


async def collect_wifi() -> dict[str, Any]:
    """Collect WiFi signal strength and related metrics."""
    ts = time.time()
    result: dict[str, Any] = {
        "ts": ts,
        "ssid": None,
        "rssi_dbm": None,
        "noise_dbm": None,
        "channel": None,
        "link_speed_mbps": None,
        "tx_rate_mbps": None,
        "error": None,
    }

    if sys.platform == "darwin":
        await _wifi_macos(result)
    else:
        await _wifi_linux(result)

    return result


async def _wifi_macos(result: dict[str, Any]) -> None:
    airport = (
        "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
    )
    if not shutil.which(airport) and not __import__("os").path.exists(airport):
        result["error"] = "airport utility not found"
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            airport,
            "-I",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        text = stdout.decode(errors="replace")

        for line in text.splitlines():
            line = line.strip()
            if line.startswith("SSID:"):
                result["ssid"] = line.split(":", 1)[1].strip()
            elif line.startswith("agrCtlRSSI:"):
                result["rssi_dbm"] = float(line.split(":")[1].strip())
            elif line.startswith("agrCtlNoise:"):
                result["noise_dbm"] = float(line.split(":")[1].strip())
            elif line.startswith("channel:"):
                # May be "6,1" or just "6"
                import contextlib

                ch = line.split(":")[1].strip().split(",")[0]
                with contextlib.suppress(ValueError):
                    result["channel"] = int(ch)
            elif line.startswith("lastTxRate:"):
                result["tx_rate_mbps"] = float(line.split(":")[1].strip())
            elif line.startswith("maxRate:"):
                result["link_speed_mbps"] = float(line.split(":")[1].strip())
    except asyncio.TimeoutError:
        result["error"] = "airport timeout"
    except Exception as e:
        result["error"] = str(e)


async def _wifi_linux(result: dict[str, Any]) -> None:
    # Try iw first, then iwconfig
    iw = shutil.which("iw")
    if iw:
        try:
            proc = await asyncio.create_subprocess_exec(
                iw,
                "dev",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            text = stdout.decode(errors="replace")
            # Find interface name
            iface = None
            for line in text.splitlines():
                m = re.search(r"Interface\s+(\S+)", line)
                if m:
                    iface = m.group(1)
                    break
            if iface:
                proc2 = await asyncio.create_subprocess_exec(
                    iw,
                    "dev",
                    iface,
                    "link",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=5)
                text2 = stdout2.decode(errors="replace")
                for line in text2.splitlines():
                    line = line.strip()
                    if "SSID:" in line:
                        result["ssid"] = line.split("SSID:")[1].strip()
                    elif "signal:" in line:
                        m = re.search(r"signal:\s*([-\d.]+)", line)
                        if m:
                            result["rssi_dbm"] = float(m.group(1))
                    elif "tx bitrate:" in line:
                        m = re.search(r"([\d.]+)\s+MBit/s", line)
                        if m:
                            result["tx_rate_mbps"] = float(m.group(1))
        except Exception as e:
            result["error"] = str(e)
        return

    iwconfig = shutil.which("iwconfig")
    if iwconfig:
        try:
            proc = await asyncio.create_subprocess_exec(
                iwconfig,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            text = stdout.decode(errors="replace")
            m = re.search(r'ESSID:"([^"]*)"', text)
            if m:
                result["ssid"] = m.group(1)
            m = re.search(r"Signal level=([-\d]+)\s*dBm", text)
            if m:
                result["rssi_dbm"] = float(m.group(1))
            m = re.search(r"Bit Rate=([\d.]+)\s*Mb/s", text)
            if m:
                result["tx_rate_mbps"] = float(m.group(1))
        except Exception as e:
            result["error"] = str(e)
        return

    result["error"] = "no WiFi tool available (iw/iwconfig)"
