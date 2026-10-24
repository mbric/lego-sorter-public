#!/usr/bin/env python3
"""Scan the local LAN for igus ReBeL (CRI port 3920, SSH 22). No iRC required."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import os
import socket
import subprocess
import sys

CRI_PORT = 3920
SSH_PORT = 22
CONNECT_TIMEOUT = 0.35
DEFAULT_SUBNET = os.getenv("ROBOT_SCAN_SUBNET", "192.168.4.0/24")


def local_ip_on_subnet(subnet: ipaddress.IPv4Network) -> str | None:
    try:
        out = subprocess.check_output(["ipconfig", "getifaddr", "en0"], text=True).strip()
        ip = ipaddress.IPv4Address(out)
        if ip in subnet:
            return str(ip)
    except (subprocess.CalledProcessError, ValueError):
        pass
    return None


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT):
            return True
    except OSError:
        return False


def scan_host(host: str) -> tuple[str, list[int]] | None:
    open_ports = [p for p in (CRI_PORT, SSH_PORT) if port_open(host, p)]
    if open_ports:
        return host, open_ports
    return None


def main() -> int:
    subnet = ipaddress.ip_network(DEFAULT_SUBNET, strict=False)
    my_ip = local_ip_on_subnet(subnet)
    print(f"Scanning {subnet} for ports {CRI_PORT} (CRI) and {SSH_PORT} (SSH)...")
    if my_ip:
        print(f"This Mac: {my_ip}")
    else:
        print("Warning: Mac en0 is not on this subnet — set ROBOT_SCAN_SUBNET if needed.")

    candidates = [str(ip) for ip in subnet.hosts()]
    hits: list[tuple[str, list[int]]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        for result in pool.map(scan_host, candidates):
            if result:
                hits.append(result)

    if not hits:
        print("\nNo hosts with CRI or SSH found.")
        print("Check: robot powered on, Eero app for new devices, cable in LAN port.")
        print("If WiFi was configured: try ping 192.168.4.200")
        print("If eth0 is still 192.168.3.11, one iRC session is needed to fix the IP.")
        return 1

    print(f"\nFound {len(hits)} candidate(s):")
    for host, ports in sorted(hits):
        labels = []
        if CRI_PORT in ports:
            labels.append("CRI")
        if SSH_PORT in ports:
            labels.append("SSH")
        print(f"  {host}  [{', '.join(labels)}]")

    cri_hosts = [h for h, p in hits if CRI_PORT in p]
    if cri_hosts:
        print(f"\nUse: export ROBOT_IP={cri_hosts[0]}")
        print("Then: python run_robot_smoke_test.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
