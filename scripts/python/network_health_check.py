#!/usr/bin/env python3
"""
network_health_check.py
-----------------------
Scans the lab network for active hosts, checks gateway/DNS reachability,
tests DNS resolution, and reports latency between VMs.

Usage:
    python network_health_check.py
    python network_health_check.py --subnet 192.168.56.0/24
    python network_health_check.py --subnet 192.168.56.0/24 --output net_report.json
"""

import argparse
import ipaddress
import json
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime


# Lab defaults
DEFAULT_SUBNET  = "192.168.56.0/24"
DEFAULT_GATEWAY = "192.168.56.1"
DEFAULT_DNS     = ["8.8.8.8", "1.1.1.1", "192.168.56.1"]
DNS_TEST_NAMES  = ["google.com", "github.com", "microsoft.com"]
PING_TIMEOUT    = 1   # seconds
SCAN_WORKERS    = 50


def ping(host: str, count: int = 2) -> tuple[bool, float]:
    """Ping host. Returns (reachable, avg_ms)."""
    param = "-n" if sys.platform == "win32" else "-c"
    w_param = "-W" if sys.platform != "win32" else "-w"
    w_val   = "1" if sys.platform != "win32" else "1000"
    try:
        start = time.time()
        result = subprocess.run(
            ["ping", param, str(count), w_param, w_val, host],
            capture_output=True, text=True, timeout=count + 3
        )
        elapsed_ms = (time.time() - start) * 1000 / count
        return result.returncode == 0, round(elapsed_ms, 1)
    except Exception:
        return False, 0.0


def resolve_dns(hostname: str, dns_server: str = None) -> tuple[bool, str, float]:
    """Resolve hostname. Returns (success, ip, latency_ms)."""
    try:
        start = time.time()
        ip = socket.gethostbyname(hostname)
        elapsed_ms = (time.time() - start) * 1000
        return True, ip, round(elapsed_ms, 1)
    except Exception as e:
        return False, str(e), 0.0


def reverse_dns(ip: str) -> str:
    """Reverse DNS lookup."""
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""


def scan_host(ip: str) -> dict | None:
    """Ping single IP. Returns dict if alive, else None."""
    alive, latency_ms = ping(ip, count=1)
    if not alive:
        return None
    hostname = reverse_dns(ip)
    return {
        "ip":         ip,
        "hostname":   hostname,
        "latency_ms": latency_ms,
        "alive":      True,
    }


def scan_subnet(subnet: str) -> list[dict]:
    """Scan entire subnet for live hosts."""
    network = ipaddress.ip_network(subnet, strict=False)
    hosts   = [str(h) for h in network.hosts()]
    print(f"Scanning {len(hosts)} hosts in {subnet}...")

    alive = []
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as executor:
        futures = {executor.submit(scan_host, ip): ip for ip in hosts}
        done = 0
        for future in as_completed(futures):
            done += 1
            result = future.result()
            if result:
                alive.append(result)
            if done % 25 == 0 or done == len(hosts):
                print(f"  Progress: {done}/{len(hosts)} scanned, {len(alive)} alive", end="\r")

    print()
    alive.sort(key=lambda x: ipaddress.ip_address(x["ip"]))
    return alive


def check_gateway(gateway: str) -> dict:
    """Check gateway reachability."""
    alive, latency = ping(gateway)
    return {
        "host":       gateway,
        "reachable":  alive,
        "latency_ms": latency,
    }


def check_dns_servers(dns_servers: list[str]) -> list[dict]:
    """Ping each DNS server."""
    results = []
    for dns in dns_servers:
        alive, latency = ping(dns)
        results.append({
            "server":     dns,
            "reachable":  alive,
            "latency_ms": latency,
        })
    return results


def check_dns_resolution(test_names: list[str]) -> list[dict]:
    """Test DNS resolution for known hostnames."""
    results = []
    for name in test_names:
        success, ip, latency = resolve_dns(name)
        results.append({
            "hostname":   name,
            "resolved":   success,
            "ip":         ip,
            "latency_ms": latency,
        })
    return results


def print_report(report: dict):
    ts = report["timestamp"]
    print(f"\n{'='*60}")
    print(f"  NETWORK HEALTH REPORT  |  {ts}")
    print(f"{'='*60}")

    # Gateway
    gw = report["gateway"]
    gw_state = f"REACHABLE ({gw['latency_ms']}ms)" if gw["reachable"] else "UNREACHABLE"
    print(f"\n  Gateway ({gw['host']}): {gw_state}")

    # DNS servers
    print("\n  DNS Servers:")
    for d in report["dns_servers"]:
        state = f"UP ({d['latency_ms']}ms)" if d["reachable"] else "DOWN"
        print(f"    {d['server']:20} {state}")

    # DNS resolution
    print("\n  DNS Resolution Tests:")
    for r in report["dns_resolution"]:
        if r["resolved"]:
            print(f"    {r['hostname']:25} -> {r['ip']}  ({r['latency_ms']}ms)")
        else:
            print(f"    {r['hostname']:25} -> FAILED: {r['ip']}")

    # Live hosts
    hosts = report["live_hosts"]
    print(f"\n  Live Hosts on {report['subnet']}:  ({len(hosts)} found)")
    if hosts:
        print(f"    {'IP':20} {'Hostname':30} {'Latency'}")
        print(f"    {'-'*60}")
        for h in hosts:
            hn = h["hostname"] or "-"
            print(f"    {h['ip']:20} {hn:30} {h['latency_ms']}ms")
    else:
        print("    No live hosts found.")

    # Summary
    dns_ok  = sum(1 for d in report["dns_servers"] if d["reachable"])
    res_ok  = sum(1 for r in report["dns_resolution"] if r["resolved"])
    print(f"\n{'='*60}")
    print(f"  Gateway: {'OK' if gw['reachable'] else 'FAIL'}  |  "
          f"DNS Servers: {dns_ok}/{len(report['dns_servers'])} up  |  "
          f"Resolution: {res_ok}/{len(report['dns_resolution'])} OK  |  "
          f"Hosts: {len(hosts)} alive")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Lab network health check")
    parser.add_argument("--subnet",  default=DEFAULT_SUBNET,  help="Subnet to scan (CIDR)")
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY, help="Gateway IP")
    parser.add_argument("--dns",     default=",".join(DEFAULT_DNS), help="Comma-separated DNS servers")
    parser.add_argument("--output",  default=None, help="Save JSON report to file")
    parser.add_argument("--no-scan", action="store_true", help="Skip host discovery scan")
    args = parser.parse_args()

    dns_servers = [d.strip() for d in args.dns.split(",")]

    report = {
        "timestamp":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "subnet":         args.subnet,
        "gateway":        check_gateway(args.gateway),
        "dns_servers":    check_dns_servers(dns_servers),
        "dns_resolution": check_dns_resolution(DNS_TEST_NAMES),
        "live_hosts":     [] if args.no_scan else scan_subnet(args.subnet),
    }

    print_report(report)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        print(f"Report saved to: {args.output}")


if __name__ == "__main__":
    main()
