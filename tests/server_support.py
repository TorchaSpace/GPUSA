"""A tiny fake of the cloud API (server/) for the client and Admin-page tests."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PRODUCTS = [
    {"barcode": "A1", "name": "Anvil", "price": 9.5, "stock_quantity": 3, "critical_stock_level": 1, "is_active": True},
    {"barcode": "B2", "name": "Bolt", "price": 0.25, "stock_quantity": 40, "critical_stock_level": 5, "is_active": True},
]


class FakeServer:
    """Accepts badge B-001 / PIN 482915. `employees_allowed=False` answers 403 on /employees."""

    def __init__(self, employees_allowed: bool = True):
        self.employees_allowed = employees_allowed
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
                self._send(404, {"detail": "Not found"})

        self._httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_port}"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()
