"""DNS resolution collector."""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any


async def resolve_domain(
    domain: str,
    resolver: str,
    timeout: int = 5,
) -> dict[str, Any]:
    """Resolve a domain against a specific resolver."""
    ts = time.time()
    result: dict[str, Any] = {
        "ts": ts,
        "domain": domain,
        "resolver": resolver,
        "resolve_ms": None,
        "status": "unknown",
        "resolved_ip": None,
        "error": None,
    }

    if resolver == "system":
        await _resolve_system(domain, timeout, result)
    else:
        await _resolve_custom(domain, resolver, timeout, result)

    return result


async def _resolve_system(domain: str, timeout: int, result: dict[str, Any]) -> None:
    """Use system resolver via asyncio."""
    t0 = time.perf_counter()
    try:
        loop = asyncio.get_event_loop()
        infos = await asyncio.wait_for(
            loop.getaddrinfo(domain, None, family=socket.AF_INET),
            timeout=timeout,
        )
        result["resolve_ms"] = (time.perf_counter() - t0) * 1000
        if infos:
            result["status"] = "ok"
            result["resolved_ip"] = infos[0][4][0]
        else:
            result["status"] = "nxdomain"
    except asyncio.TimeoutError:
        result["resolve_ms"] = timeout * 1000
        result["status"] = "timeout"
        result["error"] = "timeout"
    except socket.gaierror as e:
        result["resolve_ms"] = (time.perf_counter() - t0) * 1000
        if "NXDOMAIN" in str(e) or e.errno in (socket.EAI_NONAME, -2, 8):
            result["status"] = "nxdomain"
        else:
            result["status"] = "error"
        result["error"] = str(e)
    except Exception as e:
        result["resolve_ms"] = (time.perf_counter() - t0) * 1000
        result["status"] = "error"
        result["error"] = str(e)


async def _resolve_custom(
    domain: str, resolver_ip: str, timeout: int, result: dict[str, Any]
) -> None:
    """Use a specific DNS resolver via raw UDP query (port 53)."""

    # Build a minimal DNS query for A record
    query = _build_dns_query(domain)
    t0 = time.perf_counter()

    try:
        response = await asyncio.wait_for(
            _udp_dns_query(resolver_ip, 53, query), timeout=timeout
        )
        result["resolve_ms"] = (time.perf_counter() - t0) * 1000
        ip = _parse_dns_response(response)
        if ip:
            result["status"] = "ok"
            result["resolved_ip"] = ip
        else:
            result["status"] = "nxdomain"
    except asyncio.TimeoutError:
        result["resolve_ms"] = timeout * 1000
        result["status"] = "timeout"
        result["error"] = "timeout"
    except Exception as e:
        result["resolve_ms"] = (time.perf_counter() - t0) * 1000
        result["status"] = "error"
        result["error"] = str(e)


def _build_dns_query(domain: str) -> bytes:
    """Build a minimal DNS A-record query."""
    import random
    import struct

    txid = random.randint(0, 65535)
    flags = 0x0100  # Standard query, recursion desired
    qdcount = 1
    header = struct.pack(">HHHHHH", txid, flags, qdcount, 0, 0, 0)

    question = b""
    for part in domain.split("."):
        encoded = part.encode()
        question += bytes([len(encoded)]) + encoded
    question += b"\x00"  # null terminator
    question += struct.pack(">HH", 1, 1)  # QTYPE=A, QCLASS=IN

    return header + question


async def _udp_dns_query(host: str, port: int, query: bytes) -> bytes:
    """Send DNS query over UDP and return response."""
    loop = asyncio.get_event_loop()

    class DNSProtocol(asyncio.DatagramProtocol):
        def __init__(self) -> None:
            self.future: asyncio.Future[bytes] = loop.create_future()

        def datagram_received(self, data: bytes, addr: tuple) -> None:
            if not self.future.done():
                self.future.set_result(data)

        def error_received(self, exc: Exception) -> None:
            if not self.future.done():
                self.future.set_exception(exc)

        def connection_lost(self, exc: Exception | None) -> None:
            if not self.future.done():
                self.future.set_exception(exc or ConnectionError("connection lost"))

    transport, protocol = await loop.create_datagram_endpoint(
        DNSProtocol, remote_addr=(host, port)
    )
    try:
        transport.sendto(query)
        return await protocol.future
    finally:
        transport.close()


def _parse_dns_response(data: bytes) -> str | None:
    """Extract first A record IP from a DNS response."""
    import struct

    if len(data) < 12:
        return None

    _txid, flags, qdcount, ancount, _, _ = struct.unpack(">HHHHHH", data[:12])
    rcode = flags & 0x000F
    if rcode != 0 or ancount == 0:
        return None

    offset = 12
    # Skip questions
    for _ in range(qdcount):
        while offset < len(data) and data[offset] != 0:
            if data[offset] & 0xC0 == 0xC0:
                offset += 2
                break
            offset += data[offset] + 1
        else:
            offset += 1
        offset += 4  # QTYPE + QCLASS

    # Parse answers
    for _ in range(ancount):
        if offset >= len(data):
            break
        # Skip name (may be compressed)
        if data[offset] & 0xC0 == 0xC0:
            offset += 2
        else:
            while offset < len(data) and data[offset] != 0:
                offset += data[offset] + 1
            offset += 1

        if offset + 10 > len(data):
            break

        rtype, rclass, ttl, rdlength = struct.unpack(">HHIH", data[offset : offset + 10])
        offset += 10

        if rtype == 1 and rdlength == 4:  # A record
            ip = ".".join(str(b) for b in data[offset : offset + 4])
            return ip

        offset += rdlength

    return None


async def collect_dns_round(
    domains: list[str],
    resolvers: list[str],
    timeout: int = 5,
) -> list[dict[str, Any]]:
    """Run all domain/resolver combinations concurrently."""
    tasks = [
        resolve_domain(domain, resolver, timeout)
        for domain in domains
        for resolver in resolvers
    ]
    return list(await asyncio.gather(*tasks))
