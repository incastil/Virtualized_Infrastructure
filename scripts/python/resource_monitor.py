#!/usr/bin/env python3
"""
resource_monitor.py
-------------------
Continuous resource monitor for lab VMs. Tracks CPU, RAM, disk, and network
metrics. Supports threshold alerts, periodic sampling, and JSON/CSV output.

Requirements:
    pip install psutil

Usage:
    python resource_monitor.py
    python resource_monitor.py --interval 10 --duration 60
    python resource_monitor.py --interval 5 --output metrics.csv --alert-cpu 80
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime

try:
    import psutil
except ImportError:
    print("ERROR: psutil not installed. Run: pip install psutil")
    sys.exit(1)


# Alert thresholds (percent)
DEFAULT_CPU_THRESHOLD  = 85.0
DEFAULT_RAM_THRESHOLD  = 90.0
DEFAULT_DISK_THRESHOLD = 85.0


def get_cpu_metrics() -> dict:
    """CPU usage, frequency, per-core stats."""
    freq = psutil.cpu_freq()
    return {
        "usage_pct":       psutil.cpu_percent(interval=0.5),
        "per_core_pct":    psutil.cpu_percent(interval=0.5, percpu=True),
        "logical_cores":   psutil.cpu_count(logical=True),
        "physical_cores":  psutil.cpu_count(logical=False),
        "freq_mhz_current": round(freq.current, 1) if freq else None,
        "freq_mhz_max":     round(freq.max, 1) if freq else None,
    }


def get_ram_metrics() -> dict:
    """Virtual and swap memory stats."""
    vm   = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        "total_gb":   round(vm.total / 1e9, 2),
        "used_gb":    round(vm.used / 1e9, 2),
        "free_gb":    round(vm.available / 1e9, 2),
        "usage_pct":  vm.percent,
        "swap_total_gb": round(swap.total / 1e9, 2),
        "swap_used_gb":  round(swap.used / 1e9, 2),
        "swap_pct":      swap.percent,
    }


def get_disk_metrics() -> list[dict]:
    """Disk usage per mounted partition."""
    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            io    = psutil.disk_io_counters(perdisk=True)
            dev   = part.device.replace("/dev/", "").replace("\\", "").replace(":", "")
            dev_io = io.get(dev, None) if io else None
            disks.append({
                "device":      part.device,
                "mountpoint":  part.mountpoint,
                "fstype":      part.fstype,
                "total_gb":    round(usage.total / 1e9, 2),
                "used_gb":     round(usage.used / 1e9, 2),
                "free_gb":     round(usage.free / 1e9, 2),
                "usage_pct":   usage.percent,
                "read_mb":     round(dev_io.read_bytes / 1e6, 1) if dev_io else None,
                "write_mb":    round(dev_io.write_bytes / 1e6, 1) if dev_io else None,
            })
        except PermissionError:
            continue
    return disks


def get_network_metrics() -> dict:
    """Network I/O counters and active connections."""
    net = psutil.net_io_counters()
    conns = psutil.net_connections(kind="inet")
    return {
        "bytes_sent_mb":    round(net.bytes_sent / 1e6, 2),
        "bytes_recv_mb":    round(net.bytes_recv / 1e6, 2),
        "packets_sent":     net.packets_sent,
        "packets_recv":     net.packets_recv,
        "err_in":           net.errin,
        "err_out":          net.errout,
        "drop_in":          net.dropin,
        "drop_out":         net.dropout,
        "active_connections": len([c for c in conns if c.status == "ESTABLISHED"]),
        "listening_ports":    len([c for c in conns if c.status == "LISTEN"]),
    }


def get_top_processes(n: int = 5) -> list[dict]:
    """Top N processes by CPU usage."""
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            procs.append(p.info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    procs.sort(key=lambda x: x.get("cpu_percent") or 0, reverse=True)
    return procs[:n]


def collect_snapshot() -> dict:
    """Collect all metrics into one snapshot dict."""
    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cpu":       get_cpu_metrics(),
        "ram":       get_ram_metrics(),
        "disks":     get_disk_metrics(),
        "network":   get_network_metrics(),
        "top_procs": get_top_processes(),
    }


def check_alerts(snap: dict, cpu_thr: float, ram_thr: float, disk_thr: float) -> list[str]:
    """Return list of alert strings for breached thresholds."""
    alerts = []
    if snap["cpu"]["usage_pct"] >= cpu_thr:
        alerts.append(f"CPU {snap['cpu']['usage_pct']}% >= {cpu_thr}% threshold")
    if snap["ram"]["usage_pct"] >= ram_thr:
        alerts.append(f"RAM {snap['ram']['usage_pct']}% >= {ram_thr}% threshold")
    for disk in snap["disks"]:
        if disk["usage_pct"] >= disk_thr:
            alerts.append(f"Disk {disk['mountpoint']} {disk['usage_pct']}% >= {disk_thr}% threshold")
    return alerts


def print_snapshot(snap: dict, alerts: list[str]):
    """Print a formatted snapshot to console."""
    cpu = snap["cpu"]
    ram = snap["ram"]
    net = snap["network"]

    print(f"\n[{snap['timestamp']}]")
    print(f"  CPU:  {cpu['usage_pct']:5.1f}%  | Cores: {cpu['physical_cores']}p/{cpu['logical_cores']}l"
          f"  | Freq: {cpu['freq_mhz_current']}MHz")
    print(f"  RAM:  {ram['usage_pct']:5.1f}%  | {ram['used_gb']}GB / {ram['total_gb']}GB"
          f"  | Swap: {ram['swap_pct']}%")
    for disk in snap["disks"]:
        print(f"  Disk: {disk['mountpoint']:12} {disk['usage_pct']:5.1f}%  | "
              f"{disk['used_gb']}GB / {disk['total_gb']}GB free {disk['free_gb']}GB")
    print(f"  Net:  Sent {net['bytes_sent_mb']}MB  Recv {net['bytes_recv_mb']}MB"
          f"  | Conns: {net['active_connections']} active")
    if alerts:
        for a in alerts:
            print(f"  *** ALERT: {a} ***")


def write_csv_row(writer, snap: dict):
    """Write flat CSV row from snapshot."""
    cpu = snap["cpu"]
    ram = snap["ram"]
    net = snap["network"]
    primary_disk = snap["disks"][0] if snap["disks"] else {}
    writer.writerow({
        "timestamp":       snap["timestamp"],
        "cpu_pct":         cpu["usage_pct"],
        "ram_pct":         ram["usage_pct"],
        "ram_used_gb":     ram["used_gb"],
        "ram_total_gb":    ram["total_gb"],
        "disk_pct":        primary_disk.get("usage_pct", ""),
        "disk_free_gb":    primary_disk.get("free_gb", ""),
        "net_sent_mb":     net["bytes_sent_mb"],
        "net_recv_mb":     net["bytes_recv_mb"],
        "active_conns":    net["active_connections"],
    })


CSV_FIELDS = ["timestamp","cpu_pct","ram_pct","ram_used_gb","ram_total_gb",
              "disk_pct","disk_free_gb","net_sent_mb","net_recv_mb","active_conns"]


def main():
    parser = argparse.ArgumentParser(description="VM resource monitor")
    parser.add_argument("--interval",   type=int,   default=5,    help="Sampling interval in seconds")
    parser.add_argument("--duration",   type=int,   default=0,    help="Total run time in seconds (0=forever)")
    parser.add_argument("--output",     default=None,             help="CSV output file")
    parser.add_argument("--json-log",   default=None,             help="Append snapshots to JSON lines file")
    parser.add_argument("--alert-cpu",  type=float, default=DEFAULT_CPU_THRESHOLD,  help="CPU alert threshold %")
    parser.add_argument("--alert-ram",  type=float, default=DEFAULT_RAM_THRESHOLD,  help="RAM alert threshold %")
    parser.add_argument("--alert-disk", type=float, default=DEFAULT_DISK_THRESHOLD, help="Disk alert threshold %")
    args = parser.parse_args()

    print(f"Resource Monitor started | interval={args.interval}s | "
          f"alerts: CPU>{args.alert_cpu}% RAM>{args.alert_ram}% Disk>{args.alert_disk}%")
    print(f"Duration: {'forever' if args.duration == 0 else f'{args.duration}s'}")
    if args.output:
        print(f"CSV output: {args.output}")
    print("Press Ctrl+C to stop.\n")

    csv_file   = None
    csv_writer = None

    if args.output:
        csv_file   = open(args.output, "w", newline="")
        csv_writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        csv_writer.writeheader()

    start_time = time.time()
    samples    = 0

    try:
        while True:
            snap   = collect_snapshot()
            alerts = check_alerts(snap, args.alert_cpu, args.alert_ram, args.alert_disk)
            print_snapshot(snap, alerts)

            if csv_writer:
                write_csv_row(csv_writer, snap)
                csv_file.flush()

            if args.json_log:
                with open(args.json_log, "a") as jf:
                    jf.write(json.dumps(snap) + "\n")

            samples += 1

            if args.duration > 0 and (time.time() - start_time) >= args.duration:
                break

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\nStopped by user.")

    finally:
        if csv_file:
            csv_file.close()

    elapsed = round(time.time() - start_time, 1)
    print(f"\nMonitoring complete. {samples} sample(s) in {elapsed}s.")


if __name__ == "__main__":
    main()
