"""Find the GPUSA server on the local network.

The server PC answers a UDP broadcast (server/gpusa_server/discovery.py). A reply is only believed after the
address it came from also answers GET /health as a GPUSA API, so a stray device that happens to answer UDP
cannot make the apps point at nothing. If more than one server answers, nothing is chosen: guessing which
business a PC belongs to would be worse than asking."""

from __future__ import annotations

import json
import socket
import time
import urllib.request

DEFAULT_PORT = 47808
QUESTION = b"GPUSA-DISCOVER?"


def _local_broadcasts() -> list[str]:
    """The global broadcast, this PC itself, and its own /24 broadcast (some systems drop the global one)."""
    targets = ["255.255.255.255", "127.0.0.1"]  # 127.0.0.1: the server runs on this very PC
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))  # no packet is sent; it only picks the outgoing interface
        mine = probe.getsockname()[0]
        probe.close()
        if mine and not mine.startswith("127."):
            targets.append(".".join(mine.split(".")[:3] + ["255"]))
    except OSError:
        pass
    return targets


def is_gpusa(url: str, timeout: float = 2.0) -> bool:
    """Whether a GPUSA API answers GET /health at `url` (never asked of an address the apps may not use)."""
    from shared.server_client import insecure_problem

    if insecure_problem(url):
        return False
    try:
        with urllib.request.urlopen(url + "/health", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")).get("status") == "ok"
    except (OSError, ValueError, AttributeError):
        return False


def find_servers(port: int = DEFAULT_PORT, timeout: float = 1.5, targets: list[str] | None = None) -> list[str]:
    """Every GPUSA API that answered, as 'http://ip:port' (empty when none)."""
    found: dict[str, None] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.2)
        for target in targets if targets is not None else _local_broadcasts():
            try:
                sock.sendto(QUESTION, (target, port))
            except OSError:
                continue
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, (ip, _) = sock.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                info = json.loads(data.decode("utf-8"))
                api_port = int(info["port"])
            except (ValueError, KeyError, TypeError):
                continue
            if info.get("gpusa") == 1 and 0 < api_port < 65536:
                found.setdefault(f"http://{ip}:{api_port}", None)
    finally:
        sock.close()
    return [url for url in found if is_gpusa(url, 2.0)]


def find_server(port: int = DEFAULT_PORT, timeout: float = 1.5, targets: list[str] | None = None) -> str | None:
    """The one server on the network, or None when there is none or there are several. A server on this
    very PC answers both on 127.0.0.1 and on the PC's own network address: that is one server, and it wins."""
    servers = find_servers(port, timeout, targets)
    local = [url for url in servers if url.startswith("http://127.")]
    if local:
        return local[0]
    return servers[0] if len(servers) == 1 else None
