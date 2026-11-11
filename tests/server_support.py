"""A tiny fake of the cloud API (server/) for the client and Admin-page tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qsl, urlsplit


def pairing_key(secret: str, device: str) -> str:
    return hmac.new(secret.encode(), b"gpusa-pairing-v1:" + device.encode(), hashlib.sha256).hexdigest()

PRODUCTS = [
    {"barcode": "A1", "name": "Anvil", "price": 9.5, "stock_quantity": 3, "critical_stock_level": 1, "is_active": True},
    {"barcode": "B2", "name": "Bolt", "price": 0.25, "stock_quantity": 40, "critical_stock_level": 5, "is_active": True},
]


class FakeServer:
    """Accepts badge B-001 / PIN 482915. `employees_allowed=False` answers 403 on /employees."""

    def __init__(self, employees_allowed: bool = True, secret: str | None = None, tls: str | None = None):
        """`tls`: "a" or "b" serves https with the test certificate in tests/data/tls/<tls>/."""
        self.employees_allowed = employees_allowed
        self.secret = secret  # with one, the fake proves itself like server/gpusa_server/identity_routes.py
        self.requests: list[tuple[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output quiet
                pass

            def _send(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                outer.requests.append(("POST", self.path))
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                body = json.loads(raw or b"{}")
                if self.path == "/auth/login" and body.get("badge_id") == "B-001" and body.get("pin") == "482915":
                    self._send(200, {"token": "tok", "badge_id": "B-001", "name": "Ali", "role": "admin",
                                     "area": body.get("area", "admin")})
                else:
                    self._send(401, {"detail": "Badge or PIN is wrong."})

            def do_GET(self):
                outer.requests.append(("GET", self.path))
                if self.path == "/health":
                    return self._send(200, {"status": "ok", "version": "9.9"})
                if self.path == "/health/db":
                    return self._send(200, {"status": "ok", "database": "ok"})
                query = dict(parse_qsl(urlsplit(self.path).query))
                if outer.secret and self.path.startswith("/identity?"):
                    key = pairing_key(outer.secret, query["device"])
                    return self._send(200, {"proof": hmac.new(bytes.fromhex(key), bytes.fromhex(query["nonce"]),
                                                             hashlib.sha256).hexdigest()})
                if self.headers.get("Authorization") != "Bearer tok":
                    return self._send(401, {"detail": "Sign in first."})
                if self.path.split("?")[0] == "/products":
                    return self._send(200, PRODUCTS)
                if self.path in ("/dealerships", "/warehouses"):
                    return self._send(200, [{"code": "X"}])
                if self.path == "/employees":
                    if outer.employees_allowed:
                        return self._send(200, [{"badge_id": "B-001"}])
                    return self._send(403, {"detail": "Only administrators and depot managers may list employees."})
                if outer.secret and self.path.startswith("/auth/pairing?"):
                    return self._send(200, {"key": pairing_key(outer.secret, query["device"])})
                if self.path == "/auth/me":
                    return self._send(200, {"badge_id": "B-001", "name": "Ali", "role": "admin", "area": "admin"})
                self._send(404, {"detail": "Not found"})

        self._httpd = HTTPServer(("127.0.0.1", 0), Handler)
        scheme = "http"
        if tls:
            import ssl
            from pathlib import Path

            folder = Path(__file__).parent / "data" / "tls" / tls
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(folder / "cert.pem", folder / "key.pem")
            self._httpd.socket = context.wrap_socket(self._httpd.socket, server_side=True)
            self.cert_pem = (folder / "cert.pem").read_text()
            scheme = "https"
        self.url = f"{scheme}://127.0.0.1:{self._httpd.server_port}"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()
