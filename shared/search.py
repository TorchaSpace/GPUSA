"""The header search ("Search SKUs, orders, people", Ctrl/Cmd+K): one box
that finds a product, dealership, warehouse or employee and says which
Admin page it lives on.

Pure functions - no Qt, no database - so ranking is testable. The caller
passes in the records; this decides what matches and in what order.
Matching is case- and accent-insensitive (a Turkish "Şişli" is found by
"sisli"), and every word typed must appear somewhere in the record.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from shared.formatting import format_int
from shared.i18n import enum_label, region_label, tr
from shared.models import Dealership, Employee, Product, Warehouse

KIND_PRODUCT, KIND_DEALERSHIP, KIND_WAREHOUSE, KIND_EMPLOYEE = "Product", "Dealership", "Warehouse", "Employee"
PAGE_FOR_KIND = {
    KIND_PRODUCT: "inventory",
    KIND_DEALERSHIP: "dealerships",
    KIND_WAREHOUSE: "warehouses",
    KIND_EMPLOYEE: "workforce",
}


@dataclass(frozen=True)
class SearchHit:
    kind: str
    key: str  # barcode / dealership code / warehouse code / badge id
    title: str
    subtitle: str
    page: str  # the nav key of the page to open

    @property
    def label(self) -> str:
        return f"{self.title}  ·  {self.subtitle}" if self.subtitle else self.title


def fold(text: str | None) -> str:
    """Lower-case and strip accents; Turkish dotless "ı" and "İ" fold to "i"."""
    if not text:
        return ""
    text = text.replace("İ", "i").replace("ı", "i")
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _score(words: list[str], key: str, title: str, haystack: str) -> int | None:
    """Lower is better; None when some word is missing. An exact id match
    beats a prefix on the id, which beats a prefix on the name, which beats
    a match anywhere."""
    folded_key, folded_title = fold(key), fold(title)
    haystack = fold(haystack)
    if not all(word in haystack for word in words):
        return None
    joined = " ".join(words)
    if joined == folded_key:
        return 0
    if folded_key.startswith(joined):
        return 1
    if folded_title.startswith(joined):
        return 2
    if any(part.startswith(words[0]) for part in folded_title.split()):
        return 3
    return 4


def search(
    query: str,
    products: list[Product],
    dealerships: list[Dealership],
    warehouses: list[Warehouse],
    employees: list[Employee],
    limit: int = 30,
) -> list[SearchHit]:
    words = fold(query).split()
    if not words:
        return []
    scored: list[tuple[int, int, str, SearchHit]] = []
    order = {KIND_PRODUCT: 0, KIND_DEALERSHIP: 1, KIND_WAREHOUSE: 2, KIND_EMPLOYEE: 3}

    def add(hit: SearchHit, score: int | None) -> None:
        if score is not None:
            scored.append((score, order[hit.kind], fold(hit.title), hit))

    for p in products:
        add(SearchHit(KIND_PRODUCT, p.barcode, p.name, f"{p.barcode} · " + tr("search.in_stock").format(n=format_int(p.stock_quantity)),
                      PAGE_FOR_KIND[KIND_PRODUCT]),
            _score(words, p.barcode, p.name, f"{p.barcode} {p.name}"))
    for d in dealerships:
        add(SearchHit(KIND_DEALERSHIP, d.code, d.name, f"{d.code} · {d.city} · {region_label(d.region)}",
                      PAGE_FOR_KIND[KIND_DEALERSHIP]),
            _score(words, d.code, d.name, f"{d.code} {d.name} {d.city} {d.region} {d.manager_name or ''}"))
    for w in warehouses:
        add(SearchHit(KIND_WAREHOUSE, w.code, w.name, f"{w.code} · {w.city}" if w.city else w.code,
                      PAGE_FOR_KIND[KIND_WAREHOUSE]),
            _score(words, w.code, w.name, f"{w.code} {w.name} {w.city}"))
    for e in employees:
        add(SearchHit(KIND_EMPLOYEE, e.badge_id, e.name, f"{e.badge_id} · {e.title or enum_label('role', e.role)} · {e.location_name}",
                      PAGE_FOR_KIND[KIND_EMPLOYEE]),
            _score(words, e.badge_id, e.name, f"{e.badge_id} {e.name} {e.title or ''} {e.role} {e.location_name}"))
    scored.sort(key=lambda item: item[:3])
    return [hit for *_, hit in scored[:limit]]
