"""Find the GPUSA server on the local network.

The server PC answers a UDP broadcast (server/gpusa_server/discovery.py). A reply is only believed after the
address it came from also answers GET /health as a GPUSA API, so a stray device that happens to answer UDP
cannot make the apps point at nothing. If more than one server answers, nothing is chosen: guessing which
business a PC belongs to would be worse than asking."""

from __future__ import annotations

import json
import socket
import time
import urllib.parse
import urllib.request

DEFAULT_PORT = 47808
DEFAULT_API_PORT = 8000  # what the server scripts start uvicorn on
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
    return _health_ok(url, timeout)


def _health_ok(url: str, timeout: float) -> bool:
    try:
        with urllib.request.urlopen(url + "/health", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")).get("status") == "ok"
    except (OSError, ValueError, AttributeError):
        return False


def only_plain_http_on_internet(text: str, timeout: float = 4.0) -> bool:
    """True when the server at a typed internet address answers, but only over plain http - which the apps
    refuse there (PINs would travel in the open). Lets the sign-in screen say why instead of "no answer".
    Only GET /health is sent, which carries nothing secret."""
    from shared.server_client import insecure_problem, normalize_url

    url = normalize_url(text)
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if not parts.hostname:
        return False
    netloc = parts.netloc if port is not None else f"{parts.netloc}:{DEFAULT_API_PORT}"
    plain = urllib.parse.urlunsplit(("http", netloc, "", "", ""))
    return bool(insecure_problem(plain)) and _health_ok(plain, timeout)


def reach(text: str, timeout: float = 4.0) -> str | None:
    """The address someone typed, as the apps will use it, once a GPUSA server answers there (else None).

    People type what a browser taught them: "https://192.168.1.20:8000" for a server that speaks plain http
    on the local network (the server PC has no certificate), or the IP without the port. Those are tried as
    well - but plain http only where it is allowed anyway (a local address, see insecure_problem), so this
    never turns an internet https address into an unencrypted one."""
    from shared.server_client import insecure_problem, normalize_url

    url = normalize_url(text)
    if not url:
        return None
    try:
        parts = urllib.parse.urlsplit(url)
        has_port = parts.port is not None
    except ValueError:
        return None
    candidates = [url]
    if parts.scheme == "https":
        candidates.append(urllib.parse.urlunsplit(("http",) + tuple(parts[1:])))
    if not has_port and parts.hostname:
        candidates += [c.replace(parts.netloc, f"{parts.netloc}:{DEFAULT_API_PORT}", 1) for c in list(candidates)]
    for candidate in dict.fromkeys(candidates):
        if not insecure_problem(candidate) and is_gpusa(candidate, timeout):
            return candidate
    return None


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
