"""The server's own https certificate.

A server PC on a network that hands out public IPs (a campus, an office) cannot be reached with plain
http: the apps refuse to send PINs unencrypted to an internet address. No public authority will sign a
certificate for a bare IP, so the server makes its own the first time it starts and keeps it
(server/tls/, or GPUSA_TLS_DIR). The apps remember it at their first connection and accept only that one
afterwards (shared/tls.py), so keep the folder: a new certificate means typing the address again on every
computer. The key file is readable by its owner only."""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import os
import socket
from pathlib import Path

TLS_DIR_ENV = "GPUSA_TLS_DIR"
DEFAULT_DIR = Path(__file__).resolve().parent.parent / "tls"
CERT_NAME = "cert.pem"
KEY_NAME = "key.pem"
VALID_DAYS = 3650


def tls_dir() -> Path:
    return Path(os.environ.get(TLS_DIR_ENV) or DEFAULT_DIR)


def _local_ips() -> list[str]:
    ips = {"127.0.0.1"}
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))  # no packet is sent; it only picks the outgoing interface
        ips.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    return sorted(ips)


def ensure_certificate(directory: Path | None = None) -> tuple[Path, Path, bool]:
    """(cert, key, made_now): the existing pair, or a new self-signed one."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    directory = directory or tls_dir()
    cert_path, key_path = directory / CERT_NAME, directory / KEY_NAME
    if cert_path.is_file() and key_path.is_file():
        return cert_path, key_path, False
    directory.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "GPUSA server"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "GPUSA")])
    alt = [x509.DNSName("localhost"), x509.DNSName(socket.gethostname() or "localhost")]
    alt += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in _local_ips()]
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=VALID_DAYS))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .sign(key, hashes.SHA256()))
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path, True


def fingerprint(cert_path: Path) -> str:
    """SHA-256 of the certificate, as AA:BB:..., to compare with what a computer remembered."""
    import ssl

    der = ssl.PEM_cert_to_DER_cert(cert_path.read_text())
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))
