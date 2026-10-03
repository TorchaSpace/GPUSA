"""shared.search - the header search box."""

from shared import search
from shared.models import Dealership, Employee, Product, Warehouse

PRODUCTS = [Product("HX-2041", "Hydraulic pump, 40 bar", 10, 212, 5), Product("FL-0041", "Fuel filter", 5, 2140, 5)]
DEALERS = [Dealership("MET-01", "Metro Heavy Parts", "Metro", "Columbus", "D. Achebe"),
           Dealership("CST-04", "Şişli Equipment", "Coastal", "İstanbul")]
WAREHOUSES = [Warehouse("WH-01", "Duluth North", "Duluth"), Warehouse("WH-02", "Memphis South")]
PEOPLE = [Employee("B-100", "Murat Yılmaz", "Operations", "Warehouse", "WH-01", title="Forklift operator"),
          Employee("B-200", "Selin Kaya", "Sales & service", "Dealership", "Metro Heavy Parts")]


def _find(query, **kw):
    return search.search(query, PRODUCTS, DEALERS, WAREHOUSES, PEOPLE, **kw)


def test_blank_query_finds_nothing():
    assert _find("") == [] and _find("   ") == []


def test_fold_removes_accents_and_turkish_dots():
    assert search.fold("Şişli İstanbul Yılmaz") == "sisli istanbul yilmaz"
    assert search.fold(None) == ""


def test_finds_each_kind_and_points_at_its_page():
    kinds = {h.kind: h.page for h in _find("0") }
    assert _find("HX-2041")[0].page == "inventory"
    assert _find("met-01")[0].page == "dealerships"
    assert _find("wh-02")[0].page == "warehouses"
    assert _find("b-200")[0].page == "workforce"
    assert isinstance(kinds, dict)


def test_accent_insensitive_and_every_word_must_match():
    assert [h.key for h in _find("sisli")] == ["CST-04"]
    assert [h.key for h in _find("istanbul")] == ["CST-04"]
    assert [h.key for h in _find("murat yilmaz")] == ["B-100"]
    assert _find("murat kaya") == []


def test_exact_id_beats_prefix_beats_name_beats_anywhere():
    products = [Product("AB-1", "Other", 1, 1, 0), Product("AB-12", "Thing", 1, 1, 0),
                Product("Z-9", "AB thing", 1, 1, 0), Product("Y-9", "Big AB", 1, 1, 0)]
    keys = [h.key for h in search.search("ab-1", products, [], [], [])]
    assert keys[0] == "AB-1" and keys[1] == "AB-12"
    keys = [h.key for h in search.search("ab", products, [], [], [])]
    assert keys.index("Z-9") < keys.index("Y-9")  # name starts with it, beats name merely containing it


def test_limit_and_labels():
    many = [Product(f"P-{i}", f"Widget {i}", 1, i, 0) for i in range(50)]
    assert len(search.search("widget", many, [], [], [], limit=10)) == 10
    hit = _find("hx-2041")[0]
    assert hit.label == "Hydraulic pump, 40 bar  ·  HX-2041 · 212 in stock"
    assert _find("wh-02")[0].subtitle == "WH-02"  # no city: no dangling separator
