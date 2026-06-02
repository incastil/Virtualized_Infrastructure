#!/usr/bin/env python3
"""
check_servers.py
----------------
Server health check tool. Verifies reachability, port availability,
and basic HTTP/HTTPS endpoint health for a list of hosts.

Usage:
    python check_servers.py
    python check_servers.py --config servers.json
    python check_servers.py --output report.json
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional


# Default server definitions. Override with --config servers.json
DEFAULT_SERVERS = [
    {"name": "Windows Server 2022", "host": "192.168.56.10", "ports": [22, 3389, 5985], "http_check": None},
    {"name": "Ubuntu Server",       "host": "192.168.56.20", "ports": [22, 80],          "http_check": "http://192.168.56.20"},
]

TIMEOUT = 3  # seconds per check


def ping_host(host: str, count: int = 1) -> tuple[bool, float]:
    """Ping host. Returns (reachable, avg_ms)."""
    param = "-n" if sys.platform == "win32" else "-c"
    try:
        start = time.time()
        result = subprocess.run(
            ["ping", param, str(count), "-W", "2", host],
            capture_output=True, text=True, timeout=5
        )
        elapsed_ms = (time.time() - start) * 1000
        reachable = result.returncode == 0
        return reachable, round(elapsed_ms, 1)
    except Exception:
        return False, 0.0


def check_port(host: str, port: int, timeout: int = TIMEOUT) -> tuple[bool, float]:
    """TCP connect check. Returns (open, latency_ms)."""
    try:
        start = time.time()
        with socket.create_connection((host, port), timeout=timeout):
            elapsed_ms = (time.time() - start) * 1000
            return True, round(elapsed_ms, 1)
    except Exception:
        return False, 0.0


def check_http(url: str, timeout: int = TIMEOUT) -> tuple[bool, int, float]:
    """HTTP GET check. Returns (success, status_code, latency_ms)."""
    try:
        import urllib.request
        start = time.time()
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            elapsed_ms = (time.time() - start) * 1000
            return True, resp.status, round(elapsed_ms, 1)
    except Exception as e:
        code = getattr(e, "code", 0)
        return False, code, 0.0


def check_server(server: dict) -> dict:
    """Run all checks for a single server."""
    host = server["host"]
    name = server["name"]
    ports = server.get("ports", [])
    http_url = server.get("http_check")

    result = {
        "name":       name,
        "host":       host,
        "timestamp":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ping":       {},
        "ports":      {},
        "http":       None,
        "overall":    "UNKNOWN",
    }

    # Ping
    reachable, ping_ms = ping_host(host)
    result["ping"] = {"reachable": reachable, "latency_ms": ping_ms}

    # Port checks
    for port in ports:
        open_, latency = check_port(host, port)
        port_label = _port_label(port)
        result["ports"][port] = {"open": open_, "latency_ms": latency, "service": port_label}

    # HTTP check
    if http_url:
        ok, code, lat = check_http(http_url)
        result["http"] = {"url": http_url, "success": ok, "status_code": code, "latency_ms": lat}

    # Overall status
    if not reachable:
        result["overall"] = "DOWN"
    elif ports and not all(v["open"] for v in result["ports"].values()):
        result["overall"] = "DEGRADED"
    elif http_url and result["http"] and not result["http"]["success"]:
        result["overall"] = "DEGRADED"
    else:
        result["overall"] = "UP"

    return result


def _port_label(port: int) -> str:
    labels = {22: "SSH", 80: "HTTP", 443: "HTTPS", 3389: "RDP", 5985: "WinRM", 5986: "WinRM-S"}
    return labels.get(port, "UNKNOWN")


def print_report(results: list[dict]):
    print(f"\n{'='*60}")
    print(f"  SERVER HEALTH REPORT  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    for r in results:
        status_color = {"UP": "\033[92m", "DOWN": "\033[91m", "DEGRADED": "\033[93m"}.get(r["overall"], "")
        reset = "\033[0m"
        print(f"\n  {status_color}[{r['overall']}]{reset}  {r['name']}  ({r['host']})")

        ping = r["ping"]
        ping_str = f"{ping['latency_ms']}ms" if ping["reachable"] else "UNREACHABLE"
        print(f"    Ping:   {ping_str}")

        for port, info in r["ports"].items():
            state = "OPEN" if info["open"] else "CLOSED"
            lat   = f"  ({info['latency_ms']}ms)" if info["open"] else ""
            print(f"    Port {port:5} ({info['service']:10}): {state}{lat}")

        if r["http"]:
            h = r["http"]
            h_state = f"OK ({h['status_code']}) {h['latency_ms']}ms" if h["success"] else f"FAIL ({h['status_code']})"
            print(f"    HTTP:   {h['url']} -> {h_state}")

    up      = sum(1 for r in results if r["overall"] == "UP")
    degraded = sum(1 for r in results if r["overall"] == "DEGRADED")
    down    = sum(1 for r in results if r["overall"] == "DOWN")
    print(f"\n{'='*60}")
    print(f"  Summary: {up} UP | {degraded} DEGRADED | {down} DOWN")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Server health check tool")
    parser.add_argument("--config",  default=None, help="JSON config file with server list")
    parser.add_argument("--output",  default=None, help="Save results to JSON file")
    parser.add_argument("--workers", default=5, type=int, help="Parallel check threads (default: 5)")
    args = parser.parse_args()

    # Load server list
    if args.config and os.path.exists(args.config):
        with open(args.config) as f:
            servers = json.load(f)
        print(f"Loaded {len(servers)} server(s) from {args.config}")
    else:
        servers = DEFAULT_SERVERS
        print(f"Using default server list ({len(servers)} hosts)")

    print(f"Running checks with {args.workers} parallel worker(s)...")

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(check_server, s): s for s in servers}
        for future in as_completed(futures):
            results.append(future.result())

    # Sort by name for consistent output
    results.sort(key=lambda x: x["name"])

    print_report(results)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"Results saved to: {args.output}")


if __name__ == "__main__":
    main()
