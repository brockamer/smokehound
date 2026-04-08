"""HTTP(S) latency collector with detailed timing breakdown."""

from __future__ import annotations

import asyncio
import ssl
import time
from typing import Any
from urllib.parse import urlparse


async def probe_http(url: str, timeout: int = 10) -> dict[str, Any]:
    """Perform an HTTP GET/HEAD probe and return detailed timing."""
    ts = time.time()
    result: dict[str, Any] = {
        "ts": ts,
        "target": url,
        "status_code": None,
        "dns_ms": None,
        "connect_ms": None,
        "tls_ms": None,
        "ttfb_ms": None,
        "total_ms": None,
        "error": None,
    }

    try:
        await asyncio.wait_for(_do_probe(url, result), timeout=timeout + 2)
    except asyncio.TimeoutError:
        result["error"] = f"timeout after {timeout}s"
        if result["total_ms"] is None:
            result["total_ms"] = timeout * 1000.0
    except Exception as e:
        result["error"] = str(e)

    return result


async def _do_probe(url: str, result: dict[str, Any]) -> None:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    use_tls = parsed.scheme == "https"

    t_start = time.perf_counter()

    # DNS resolution
    loop = asyncio.get_event_loop()
    try:
        import socket

        infos = await loop.getaddrinfo(host, port, family=socket.AF_INET)
        t_dns = time.perf_counter()
        result["dns_ms"] = (t_dns - t_start) * 1000
        addr = infos[0][4]  # (ip, port)
    except Exception as e:
        result["dns_ms"] = (time.perf_counter() - t_start) * 1000
        result["error"] = f"DNS: {e}"
        return

    # TCP connect
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(addr[0], addr[1], ssl=None),
            timeout=5,
        )
        t_connect = time.perf_counter()
        result["connect_ms"] = (t_connect - t_dns) * 1000
    except Exception as e:
        result["connect_ms"] = (time.perf_counter() - t_dns) * 1000
        result["error"] = f"TCP: {e}"
        return

    # TLS handshake (upgrade connection)
    if use_tls:
        import contextlib

        try:
            ctx = ssl.create_default_context()

            # Close plain connection and open a new TLS one
            writer.close()
            await writer.wait_closed()

            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(addr[0], addr[1], ssl=ctx, server_hostname=host),
                timeout=5,
            )
            t_tls = time.perf_counter()
            result["tls_ms"] = (t_tls - t_connect) * 1000
        except Exception as e:
            result["tls_ms"] = (time.perf_counter() - t_connect) * 1000
            result["error"] = f"TLS: {e}"
            with contextlib.suppress(Exception):
                writer.close()
            return
    else:
        t_tls = t_connect
        result["tls_ms"] = 0.0

    # HTTP request
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    request = (
        f"HEAD {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"Connection: close\r\n"
        f"User-Agent: smokehound/0.1\r\n"
        f"\r\n"
    )

    try:
        writer.write(request.encode())
        await writer.drain()

        # Read status line
        line = await asyncio.wait_for(reader.readline(), timeout=10)
        t_ttfb = time.perf_counter()
        result["ttfb_ms"] = (t_ttfb - t_tls) * 1000

        status_line = line.decode(errors="replace").strip()
        if status_line.startswith("HTTP/"):
            parts = status_line.split(" ", 2)
            if len(parts) >= 2:
                result["status_code"] = int(parts[1])

        # Drain response
        while True:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=5)
            if not chunk:
                break

        t_end = time.perf_counter()
        result["total_ms"] = (t_end - t_start) * 1000

    except Exception as e:
        result["error"] = f"HTTP: {e}"
        result["total_ms"] = (time.perf_counter() - t_start) * 1000
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def collect_http_round(
    targets: list[str],
    timeout: int = 10,
) -> list[dict[str, Any]]:
    """Probe all HTTP targets concurrently."""
    tasks = [probe_http(url, timeout) for url in targets]
    return list(await asyncio.gather(*tasks))
