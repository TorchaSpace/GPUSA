"""Start the API the way a server PC should: over https with the server's own certificate.

    .venv/bin/python -m gpusa_server.run --env-file .env          (from the server/ folder)

Makes the certificate the first time (gpusa_server/tls.py), tells the network discovery to announce
https, and runs uvicorn on 0.0.0.0:8000 (GPUSA_PORT). GPUSA_TLS=0 runs plain http instead (an older
setup; the apps then only accept it on a local network address)."""

from __future__ import annotations

import argparse
import os

TLS_ENV = "GPUSA_TLS"


def tls_wanted() -> bool:
    return (os.environ.get(TLS_ENV) or "1").strip().lower() not in ("0", "false", "no", "off")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m gpusa_server.run")
    parser.add_argument("--env-file", default=None, help="settings file (.env)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=None, help="default GPUSA_PORT or 8000")
    args = parser.parse_args(argv)
    if args.env_file:
        from dotenv import load_dotenv  # comes with uvicorn[standard]

        load_dotenv(args.env_file, override=False)
    port = args.port or int(os.environ.get("GPUSA_PORT") or 8000)
    os.environ["GPUSA_PORT"] = str(port)

    import uvicorn

    options: dict = {}
    if tls_wanted():
        from . import tls

        cert, key, made = tls.ensure_certificate()
        print(("New" if made else "Using") + f" https certificate {cert}")
        print(f"  SHA-256 {tls.fingerprint(cert)}")
        options = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
        os.environ[TLS_ENV] = "1"  # the discovery reply says https (gpusa_server/discovery.py)
    else:
        os.environ[TLS_ENV] = "0"
    uvicorn.run("gpusa_server.main:app", host=args.host, port=port, **options)


if __name__ == "__main__":
    main()
