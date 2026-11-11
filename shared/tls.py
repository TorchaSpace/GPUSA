"""https to our own server, whose certificate no public authority signed.

The server PC makes its own certificate the first time it starts (server/gpusa_server/tls.py), so its
address can be https even on a network that hands out public IPs. No browser-style authority vouches for
it, so the apps do what ssh does with a host key:

* the first time this computer connects to a server (found on the network, or its address typed), the
  certificate it shows is remembered in config.json ("server_cert");
* from then on only that certificate is accepted: something else answering at the address (a man in the
  middle) fails the TLS handshake before any badge or PIN is sent;
* typing an address on purpose forgets the old certificate, like it forgets the pairing.

A server with a certificate a public authority did sign (AWS later) is checked the normal way and nothing
is remembered. A server on this very PC (127.0.0.1) is trusted as it is: nothing travels over a network.
"""

from __future__ import annotations

import ipaddress
import ssl
import urllib.parse

from shared import paths


def _split(url: str) -> tuple[str, str, int]:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme, parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _unchecked() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def pinned_context(pem: str) -> ssl.SSLContext:
    """Accepts exactly the certificate `pem` (a self-signed one is its own authority), whatever name it shows."""
    context = ssl.create_default_context(cadata=pem)
    context.check_hostname = False
    return context


def context_for(url: str) -> ssl.SSLContext | None:
    """The TLS context a request to `url` must use (None for plain http)."""
    scheme, host, _ = _split(url)
    if scheme != "https":
        return None
    if _is_loopback(host):
        return _unchecked()
    pem = paths.get_server_cert()
    if pem:
        try:
            return pinned_context(pem)
        except (ssl.SSLError, ValueError):
            pass  # a broken saved certificate: fall back to the normal check rather than accept anything
    return ssl.create_default_context()


def probe_context(url: str) -> ssl.SSLContext | None:
    """For GET /health while looking for a server that is not remembered yet: the answer carries nothing
    secret, so the certificate is not checked here. What is remembered afterwards (remember()) is."""
    scheme, _, _ = _split(url)
    return _unchecked() if scheme == "https" else None


def fetch_pem(url: str, timeout: float = 4.0) -> str | None:
    """The certificate the server at an https `url` shows right now, or None."""
    scheme, host, port = _split(url)
    if scheme != "https" or not host:
        return None
    try:
        return ssl.get_server_certificate((host, port), timeout=timeout)
    except (OSError, ValueError, ssl.SSLError):
        return None


def publicly_trusted(url: str, timeout: float = 4.0) -> bool:
    """Whether the certificate at `url` passes the normal check (a public authority signed it for that name)."""
    import socket

    scheme, host, port = _split(url)
    if scheme != "https" or not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=host):
                return True
    except (OSError, ssl.SSLError, ValueError):
        return False


def remember(url: str, replace: bool) -> None:
    """After `url` became this computer's server: keep the certificate it shows (see the module doc).
    replace=False keeps an already remembered one (the same server found at a new address must still show
    it, or every request fails); replace=True is for an address typed on purpose."""
    scheme, host, _ = _split(url)
    if scheme != "https" or _is_loopback(host):
        if replace:
            paths.set_server_cert(None)
        return
    if paths.get_server_cert() and not replace:
        return
    if publicly_trusted(url):
        paths.set_server_cert(None)
        return
    pem = fetch_pem(url)
    if pem:
        paths.set_server_cert(pem)
