"""Treasury & ledger: checks, notes, transfers and invoices."""

import pytest

pytest.importorskip("alembic")

from test_server_purchasing import second_admin_and_manager  # noqa: E402,F401
from test_server_writes import _schema, client, login, sql  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _clean_tables(client):
    sql("TRUNCATE ledger_entries, ledger_audit, purchase_orders, purchase_price_ranges RESTART IDENTITY CASCADE")


def doc(client, h, no="chk 1", **extra):
    body = {"direction": "in", "doc_type": "check", "doc_no": no, "counterparty": " Acme ", "detail": " rent ",
            "site": " wh-01 · main ", "issue_date": "2026-11-01", "due_date": "2026-11-20", "amount": 100.005}
    body.update(extra)
    return client.post("/ledger", headers=h, json=body)


def test_create_cleans_and_audits(client):
    h = login(client)
    r = doc(client, h)
    assert r.status_code == 201, r.text
    e = r.json()
    assert (e["doc_no"], e["counterparty"], e["detail"], e["site"], e["amount"], e["status"], e["created_by"]) == (
        "CHK 1", "Acme", "rent", "WH-01 · MAIN", 100.01, "pending", "Ali · B-001")
    audit = client.get(f"/ledger/{e['id']}/audit", headers=h).json()
    assert [a["action"] for a in audit] == ["created"] and audit[0]["before"] is None
    assert audit[0]["after"]["doc_no"] == "CHK 1" and audit[0]["actor_badge"] == "B-001"
    assert client.get(f"/ledger/{e['id']}", headers=h).json()["id"] == e["id"]
    assert client.get("/ledger/999", headers=h).status_code == 404


def test_duplicate_number_same_direction_and_type_only(client):
    h = login(client)
    assert doc(client, h).status_code == 201
    dup = doc(client, h, "CHK  1")
    assert dup.status_code == 409 and "already recorded" in dup.json()["detail"]
    assert doc(client, h, "chk 1", direction="out").status_code == 201
    assert doc(client, h, "chk 1", doc_type="note").status_code == 201


@pytest.mark.parametrize("bad", [{"doc_no": "  "}, {"counterparty": " "}, {"amount": 0}, {"amount": 0.004},
                                 {"amount": -1}, {"direction": "sideways"}, {"doc_type": "cash"},
                                 {"due_date": "2026-10-01"}, {"issue_date": "nope"}])
def test_validation(client, bad):
    assert doc(client, login(client), **bad).status_code == 422


def test_edit_and_delete_only_while_pending(client):
    h = login(client)
    a = doc(client, h).json()["id"]
    b = doc(client, h, "chk 2").json()["id"]
    body = {"direction": "in", "doc_type": "check", "doc_no": "chk 9", "counterparty": "Zed", "site": None,
            "issue_date": "2026-11-01", "due_date": "2026-12-01", "amount": 5}
    r = client.put(f"/ledger/{a}", headers=h, json=body)
    assert r.status_code == 200 and r.json()["doc_no"] == "CHK 9" and r.json()["site"] is None
    assert client.put(f"/ledger/{a}", headers=h, json={**body, "doc_no": "chk 2"}).status_code == 409
    assert client.put("/ledger/999", headers=h, json=body).status_code == 404
    client.post(f"/ledger/{a}/clear", headers=h)
    again = client.put(f"/ledger/{a}", headers=h, json=body)
    assert again.status_code == 409 and "it can't be edited" in again.json()["detail"]
    assert client.delete(f"/ledger/{a}", headers=h).status_code == 409
    assert client.delete(f"/ledger/{b}", headers=h).status_code == 204
    assert client.get(f"/ledger/{b}", headers=h).status_code == 404
    history = client.get(f"/ledger/{b}/audit", headers=h).json()
    assert [x["action"] for x in history] == ["created", "deleted"] and history[1]["after"] is None
    assert [x["action"] for x in client.get(f"/ledger/{a}/audit", headers=h).json()] == ["created", "edited", "cleared"]


def test_clear_endorse_reopen_rules(client):
    h = login(client)
    chk = doc(client, h).json()["id"]
    inv = doc(client, h, "inv 1", doc_type="invoice").json()["id"]
    out = doc(client, h, "chk 7", direction="out").json()["id"]
    cleared = client.post(f"/ledger/{chk}/clear", headers=h).json()
    assert cleared["status"] == "cleared" and cleared["settled_by"] == "Ali · B-001" and cleared["settled_at"]
    second = client.post(f"/ledger/{chk}/clear", headers=h)
    assert second.status_code == 409 and "it can't be cleared now" in second.json()["detail"]
    assert client.post(f"/ledger/{chk}/endorse", headers=h).status_code == 409
    reopened = client.post(f"/ledger/{chk}/reopen", headers=h).json()
    assert (reopened["status"], reopened["settled_at"], reopened["settled_by"]) == ("pending", None, None)
    assert client.post(f"/ledger/{chk}/reopen", headers=h).status_code == 409
    assert client.post(f"/ledger/{chk}/endorse", headers=h).json()["status"] == "endorsed"
    for entry in (inv, out):
        r = client.post(f"/ledger/{entry}/endorse", headers=h)
        assert r.status_code == 422 and "received check or note" in r.json()["detail"]
    assert client.post("/ledger/999/clear", headers=h).status_code == 404
    assert [x["action"] for x in client.get(f"/ledger/{chk}/audit", headers=h).json()] == [
        "created", "cleared", "reopened", "endorsed"]
    recent = client.get("/ledger/audit?limit=2", headers=h).json()
    assert len(recent) == 2 and recent[0]["id"] > recent[1]["id"]


def test_list_filters_and_order(client):
    h = login(client)
    doc(client, h, "a", due_date="2026-12-01")
    doc(client, h, "b", due_date="2026-11-10", direction="out", site="WH-02 · Spare")
    doc(client, h, "c", due_date="2026-11-05", site=None)
    assert [e["doc_no"] for e in client.get("/ledger", headers=h).json()] == ["C", "B", "A"]
    assert [e["doc_no"] for e in client.get("/ledger?direction=out", headers=h).json()] == ["B"]
    assert [e["doc_no"] for e in client.get("/ledger?site=wh-01", headers=h).json()] == ["A"]
    assert client.get("/ledger?direction=up", headers=h).status_code == 422


def test_counterparties(client):
    h = login(client)
    doc(client, h, counterparty="zeta")
    doc(client, h, "x2", counterparty="Zeta")
    doc(client, h, "x3", counterparty="alpha")
    sql("INSERT INTO purchase_price_ranges (product_barcode, min_unit_price, max_unit_price, default_supplier) "
        "VALUES ('A1', 1, 2, 'Beta Co')")
    assert client.get("/ledger/counterparties", headers=h).json() == ["alpha", "Beta Co", "Coast", "zeta"]  # Coast: a seeded dealership


def test_roles(client):
    h = login(client)
    mgr, _ = second_admin_and_manager(client, h)
    e = doc(client, h).json()["id"]
    assert doc(client, mgr, "m-1").status_code == 201  # a depot manager records and reads
    assert client.get("/ledger", headers=mgr).status_code == 200
    assert client.get("/ledger/audit", headers=mgr).status_code == 200
    for call in (lambda: client.post(f"/ledger/{e}/clear", headers=mgr), lambda: client.delete(f"/ledger/{e}", headers=mgr),
                 lambda: client.post(f"/ledger/{e}/reopen", headers=mgr)):
        assert call().status_code == 403
    sql("UPDATE accounts SET role = 'cashier' WHERE employee_id = 1")
    pos = login(client, "pos")
    assert client.get("/ledger", headers=pos).status_code == 403
    assert doc(client, pos, "p-1").status_code == 403
    assert client.get("/ledger").status_code in (401, 403)
