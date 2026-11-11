"""What a barcode / QR reader typed, turned into the product codes to look up.

A plain barcode reads as the code itself. A QR on a product often carries
more than the code: a GS1 Digital Link web address
(``https://id.gs1.org/01/08690000000012/...``) or a GS1 element string
(``(01)08690000000012(17)271231`` or the same without brackets). Both hold
the product's GTIN-14 after the "01" marker, and a shop's EAN-13 is that
number without its leading zero. Readers can also put a symbology prefix in
front (``]C1``, ``]Q3``, ``]E0``).

``candidates(raw)`` returns every code worth trying, the raw text first, so
a product whose code really is a URL still matches as typed.
"""

from __future__ import annotations

import re

_SYMBOLOGY_PREFIX = re.compile(r"^\][A-Za-z][0-9A-Za-z]")
_DIGITAL_LINK = re.compile(r"/01/(\d{8,14})(?=[/?#]|$)")
_ELEMENT_BRACKETS = re.compile(r"^\(01\)(\d{14})")
_ELEMENT_PLAIN = re.compile(r"^01(\d{14})(?=\D|\d{2}|$)")  # "01" + 14 digits, then more elements or the end


def _gtin_variants(gtin: str) -> list[str]:
    """The GTIN as read, then the shorter forms a shop may have stored it as."""
    out = [gtin]
    trimmed = gtin
    while len(trimmed) > 8 and trimmed.startswith("0"):  # GTIN-14 -> EAN-13 -> UPC-12 ...
        trimmed = trimmed[1:]
        if len(trimmed) in (13, 12, 8):
            out.append(trimmed)
    return out


def candidates(raw: str | None) -> list[str]:
    """Codes to try for one read, most literal first; [] for an empty read."""
    text = (raw or "").strip()
    if not text:
        return []
    out = [text]
    bare = _SYMBOLOGY_PREFIX.sub("", text, count=1).strip()
    if bare and bare != text:
        out.append(bare)
    gtin = None
    for pattern in (_DIGITAL_LINK, _ELEMENT_BRACKETS):
        match = pattern.search(bare)
        if match:
            gtin = match.group(1)
            break
    if gtin is None and len(bare) >= 16:
        match = _ELEMENT_PLAIN.match(bare)
        if match:
            gtin = match.group(1)
    if gtin:
        out.extend(_gtin_variants(gtin))
    seen: set[str] = set()
    return [c for c in out if not (c in seen or seen.add(c))]
