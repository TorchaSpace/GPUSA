"""Answers 'is there a GPUSA server on this network?' so the desktop apps on a new PC can find it.

A tiny UDP responder: the apps broadcast ``GPUSA-DISCOVER?`` to DISCOVERY_PORT, this answers with a JSON line
saying which HTTP port the API listens on. It carries no data and no secrets (the reply is the same for
everyone and the API itself still needs a badge and PIN). Switch it off with GPUSA_DISCOVERY=0; the port is
GPUSA_DISCOVERY_PORT (default 47808) and the API port it announces is GPUSA_PORT (default 8000). A firewall
must let UDP 47808 in on the server PC (``sudo ufw allow 47808/udp``)."""

from __future__ import annotations

import json
import logging
import os
import socket
import threading

from . import __version__

log = logging.getLogger("gpusa_server.discovery")

DEFAULT_PORT = 47808
QUESTION = b"GPUSA-DISCOVER?"


def reply(http_port: int) -> bytes:
    return json.dumps({"gpusa": 1, "port": int(http_port), "version": __version__}).encode("utf-8")


class Responder:
    def __init__(self, port: int = DEFAULT_PORT, http_port: int = 8000, host: str = ""):
        self.http_port = http_port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.settimeout(0.5)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="gpusa-discovery", daemon=True)

    def start(self) -> "Responder":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._sock.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, sender = self._sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                return
            if data.strip() == QUESTION:
                try:
                    self._sock.sendto(reply(self.http_port), sender)
                except OSError:
                    pass


def start_from_environment() -> Responder | None:
    """Started with the API. Never stops the API from starting: a busy port only costs the auto-find."""
    if os.environ.get("GPUSA_DISCOVERY", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        port = int(os.environ.get("GPUSA_DISCOVERY_PORT") or DEFAULT_PORT)
        http_port = int(os.environ.get("GPUSA_PORT") or 8000)
        responder = Responder(port, http_port).start()
    except (OSError, ValueError) as exc:
        log.warning("Network discovery is off: %s", exc)
        return None
    log.info("Answering network discovery on UDP %s (API port %s)", responder.port, http_port)
    return responder
