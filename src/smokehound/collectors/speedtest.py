"""Bandwidth spot-check collector."""

from __future__ import annotations

import asyncio
import time
from typing import Any

# Cloudflare speed test endpoints (fixed-size files, no signup)
_DOWNLOAD_URL = "https://speed.cloudflare.com/__down?bytes=10000000"  # 10 MB
_UPLOAD_URL = "https://speed.cloudflare.com/__up"


async def run_speedtest(timeout: int = 60) -> dict[str, Any]:
    """Run a lightweight bandwidth spot check."""
    ts = time.time()
    result: dict[str, Any] = {
        "ts": ts,
        "download_mbps": None,
        "upload_mbps": None,
        "ping_ms": None,
        "server": "speed.cloudflare.com",
        "error": None,
    }

    # Try speedtest-cli first
    if await _try_speedtest_cli(result, timeout):
        return result

    # Fall back to Cloudflare endpoint
    await _cloudflare_speedtest(result, timeout)
    return result


async def _try_speedtest_cli(result: dict[str, Any], timeout: int) -> bool:
    """Try using speedtest-cli if available."""
    import shutil

    if not shutil.which("speedtest-cli"):
        return False

    try:
        proc = await asyncio.create_subprocess_exec(
            "speedtest-cli",
            "--simple",
            "--secure",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            return False

        text = stdout.decode(errors="replace")
        import re

        m = re.search(r"Ping:\s+([\d.]+)\s+ms", text)
        if m:
            result["ping_ms"] = float(m.group(1))
        m = re.search(r"Download:\s+([\d.]+)\s+Mbit/s", text)
        if m:
            result["download_mbps"] = float(m.group(1))
        m = re.search(r"Upload:\s+([\d.]+)\s+Mbit/s", text)
        if m:
            result["upload_mbps"] = float(m.group(1))

        result["server"] = "speedtest-cli"
        return True
    except asyncio.TimeoutError:
        return False
    except Exception:
        return False


async def _cloudflare_speedtest(result: dict[str, Any], timeout: int) -> None:
    """Use Cloudflare speed test endpoints."""
    import ssl
    from urllib.parse import urlparse

    async def _download_bytes(url: str, max_bytes: int = 10_000_000) -> tuple[int, float]:
        """Return (bytes_received, elapsed_seconds)."""
        parsed = urlparse(url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        use_tls = parsed.scheme == "https"

        import socket

        loop = asyncio.get_event_loop()
        infos = await loop.getaddrinfo(host, port, family=socket.AF_INET)
        addr = infos[0][4]

        ctx = ssl.create_default_context() if use_tls else None
        reader, writer = await asyncio.open_connection(
            addr[0], addr[1], ssl=ctx, server_hostname=host if use_tls else None
        )

        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"Connection: close\r\n"
            f"User-Agent: smokehound/0.1\r\n\r\n"
        )
        writer.write(request.encode())
        await writer.drain()

        t0 = time.perf_counter()
        total = 0
        # Skip headers
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            if line in (b"\r\n", b"\n", b""):
                break

        while total < max_bytes:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=10)
            if not chunk:
                break
            total += len(chunk)

        import contextlib

        elapsed = time.perf_counter() - t0
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return total, elapsed

    # Download test
    try:
        bytes_recv, elapsed = await asyncio.wait_for(
            _download_bytes(_DOWNLOAD_URL), timeout=timeout // 2
        )
        if elapsed > 0:
            result["download_mbps"] = round((bytes_recv * 8) / (elapsed * 1_000_000), 2)
    except Exception as e:
        result["error"] = f"download: {e}"

    # Simple RTT ping via HTTPS connect
    try:
        import socket

        t0 = time.perf_counter()
        loop = asyncio.get_event_loop()
        infos = await loop.getaddrinfo("speed.cloudflare.com", 443, family=socket.AF_INET)
        addr = infos[0][4]
        ctx = ssl.create_default_context()
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(
                addr[0], addr[1], ssl=ctx, server_hostname="speed.cloudflare.com"
            ),
            timeout=5,
        )
        import contextlib

        result["ping_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    except Exception:
        pass
