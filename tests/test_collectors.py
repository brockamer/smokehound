"""Tests for individual collectors."""

from __future__ import annotations

import pytest

from smokehound.collectors.dns import _build_dns_query, resolve_domain
from smokehound.collectors.ping import _parse_ping_output

# ---------------------------------------------------------------------------
# Ping parsing
# ---------------------------------------------------------------------------

MACOS_PING_OUTPUT = """
PING 8.8.8.8 (8.8.8.8): 56 data bytes
64 bytes from 8.8.8.8: icmp_seq=0 ttl=119 time=11.234 ms
64 bytes from 8.8.8.8: icmp_seq=1 ttl=119 time=12.001 ms
64 bytes from 8.8.8.8: icmp_seq=2 ttl=119 time=10.987 ms
64 bytes from 8.8.8.8: icmp_seq=3 ttl=119 time=11.500 ms
64 bytes from 8.8.8.8: icmp_seq=4 ttl=119 time=13.200 ms

--- 8.8.8.8 ping statistics ---
5 packets transmitted, 5 packets received, 0.0% packet loss
round-trip min/avg/max/stddev = 10.987/11.784/13.200/0.764 ms
"""

LINUX_PING_OUTPUT = """
PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.
64 bytes from 8.8.8.8: icmp_seq=1 ttl=119 time=12.1 ms
64 bytes from 8.8.8.8: icmp_seq=2 ttl=119 time=11.5 ms
64 bytes from 8.8.8.8: icmp_seq=3 ttl=119 time=10.9 ms

--- 8.8.8.8 ping statistics ---
3 packets transmitted, 3 received, 0% packet loss, time 2002ms
rtt min/avg/max/mdev = 10.900/11.500/12.100/0.490 ms
"""

LOSS_PING_OUTPUT = """
--- 1.2.3.4 ping statistics ---
5 packets transmitted, 2 packets received, 60.0% packet loss
round-trip min/avg/max/stddev = 15.000/18.500/22.000/3.500 ms
"""


def test_parse_macos_ping():
    result = {"packets_sent": 5, "packets_recv": 0, "loss_pct": 100.0,
               "rtt_min_ms": None, "rtt_avg_ms": None, "rtt_max_ms": None, "jitter_ms": None}
    _parse_ping_output(MACOS_PING_OUTPUT, result, 5)
    assert result["loss_pct"] == 0.0
    assert result["rtt_min_ms"] == pytest.approx(10.987)
    assert result["rtt_avg_ms"] == pytest.approx(11.784)
    assert result["rtt_max_ms"] == pytest.approx(13.200)
    assert result["jitter_ms"] == pytest.approx(0.764)


def test_parse_linux_ping():
    result = {"packets_sent": 3, "packets_recv": 0, "loss_pct": 100.0,
               "rtt_min_ms": None, "rtt_avg_ms": None, "rtt_max_ms": None, "jitter_ms": None}
    _parse_ping_output(LINUX_PING_OUTPUT, result, 3)
    assert result["loss_pct"] == 0.0
    assert result["rtt_min_ms"] == pytest.approx(10.900)
    assert result["rtt_avg_ms"] == pytest.approx(11.500)


def test_parse_ping_with_loss():
    result = {"packets_sent": 5, "packets_recv": 0, "loss_pct": 100.0,
               "rtt_min_ms": None, "rtt_avg_ms": None, "rtt_max_ms": None, "jitter_ms": None}
    _parse_ping_output(LOSS_PING_OUTPUT, result, 5)
    assert result["loss_pct"] == 60.0
    assert result["rtt_avg_ms"] == pytest.approx(18.5)


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------

def test_build_dns_query_returns_bytes():
    q = _build_dns_query("google.com")
    assert isinstance(q, bytes)
    assert len(q) > 12  # At minimum header + question


def test_dns_query_structure():
    """DNS query has correct question structure."""
    q = _build_dns_query("example.com")
    # Header is 12 bytes
    # Verify it's a valid query (flags indicate QR=0, recursion desired)
    import struct
    txid, flags, qdcount = struct.unpack(">HHH", q[:6])
    assert qdcount == 1
    assert flags & 0x0100  # RD bit set


@pytest.mark.asyncio
async def test_resolve_system_dns():
    """System DNS resolution returns a result."""
    result = await resolve_domain("google.com", "system", timeout=10)
    assert result["domain"] == "google.com"
    assert result["resolver"] == "system"
    assert result["status"] in ("ok", "timeout", "error")
    if result["status"] == "ok":
        assert result["resolved_ip"] is not None
        assert result["resolve_ms"] > 0


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_http_probe_structure():
    """HTTP probe returns all expected fields."""
    from smokehound.collectors.http import probe_http

    result = await probe_http("https://www.google.com/generate_204", timeout=15)
    assert "ts" in result
    assert "target" in result
    assert "total_ms" in result
    # May fail in CI without network, just check shape
    assert result["target"] == "https://www.google.com/generate_204"


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------

def test_gateway_detection_returns_string_or_none():
    from smokehound.collectors.gateway import detect_gateway

    gw = detect_gateway()
    # Should be None or a string that looks like an IP
    if gw is not None:
        import re
        assert re.match(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", gw)


# ---------------------------------------------------------------------------
# Traceroute parsing
# ---------------------------------------------------------------------------

def test_parse_traceroute_output():
    from smokehound.collectors.traceroute import _parse_traceroute

    sample = """
traceroute to 8.8.8.8 (8.8.8.8), 30 hops max, 60 byte packets
 1  192.168.1.1  1.234 ms  1.100 ms  1.050 ms
 2  10.0.0.1  5.678 ms  5.500 ms  5.300 ms
 3  * * *
 4  8.8.8.8  12.000 ms  11.900 ms  11.800 ms
"""
    hops = _parse_traceroute(sample)
    assert len(hops) == 4
    assert hops[0]["hop"] == 1
    assert hops[0]["ip"] == "192.168.1.1"
    assert hops[2]["ip"] == "*"
    assert hops[3]["ip"] == "8.8.8.8"
