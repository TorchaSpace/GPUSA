"""Proof that this is the server a computer was connected to.

The desktop apps find the server by themselves (a UDP question on the network, see discovery.py), and
whatever answers gets the badge and PIN typed next. So a computer that has signed in here once keeps a
pairing key, and afterwards asks any address claiming to be "the server" to prove it knows that key before a
PIN is sent or a new address is believed:

* ``GET /auth/pairing?device=<id>`` (signed in): the key for that computer, ``HMAC(server secret, device id)``.
  Nothing is stored; the same computer always gets the same key from the same server.
* ``GET /identity?device=<id>&nonce=<random>`` (public): ``HMAC(key, nonce)``. Only a server holding the
  secret can answer; the answer reveals nothing about the key.

This stops an impostor that merely answers on the network. It is not a substitute for HTTPS: someone who can
sit between a computer and the real server can relay the question.
"""

from __future__ import annotations

import hashlib
import hmac
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .auth_routes import Session, current_session

router = APIRouter(tags=["identity"])

_HEX = re.compile(r"^[0-9a-f]{32,64}$")


def pairing_key(secret: str, device: str) -> str:
    return hmac.new(secret.encode(), b"gpusa-pairing-v1:" + device.encode(), hashlib.sha256).hexdigest()


def _checked(request: Request, *values: str) -> str:
    secret = request.app.state.settings.jwt_secret
    if not secret:
        raise HTTPException(status_code=503, detail="Sign-in is not configured on this server.")
    if not all(_HEX.match(value) for value in values):
        raise HTTPException(status_code=422, detail="device and nonce are 32 to 64 lower-case hex characters.")
    return secret


@router.get("/auth/pairing")
def pairing(request: Request, device: str = Query(max_length=64), _: Session = Depends(current_session)) -> dict:
    return {"key": pairing_key(_checked(request, device), device)}


@router.get("/identity")
def identity(request: Request, device: str = Query(max_length=64), nonce: str = Query(max_length=64)) -> dict:
    key = pairing_key(_checked(request, device, nonce), device)
    return {"proof": hmac.new(bytes.fromhex(key), bytes.fromhex(nonce), hashlib.sha256).hexdigest()}
