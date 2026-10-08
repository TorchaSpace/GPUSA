"""Offscreen GUI tests for admin_app's Reports page."""

from __future__ import annotations

from datetime import date

import pytest

from tests.gui_support import pump, qapp  # noqa: F401  (qapp is a fixture)

import database.connection as connection
from database import dealership_repository, product_repository, transaction_repository
from database.connection import connection_scope
from shared.models import Dealership, LineItem, Product, Transaction

TODAY = date(2026, 9, 24)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _sale(day_iso: str, amount: float, dealership_code: str | None = None) -> None:
    """One sale of `amount`, dated `day_iso` (the checkout stamps 'now'; the
    row is then moved to the day under test)."""
    # The checkout refuses a price that differs from the catalogue, so make
    # the catalogue price the amount under test first.
    with connection_scope() as conn:
        conn.execute("UPDATE products SET price = ? WHERE barcode = ?", (amount, "P1"))
        conn.commit()
    saved = transaction_repository.finalize_transaction(
        Transaction(items=[LineItem(product_barcode="P1", product_name_at_sale="Widget", unit_price_at_sale=amount, quantity=1)])
    )
    with connection_scope() as conn:
        conn.execute(
            "UPDATE transactions SET created_at = ?, dealership_code = ? WHERE id = ?",
            (f"{day_iso}T12:00:00.000Z", dealership_code, saved.id),
        )
        conn.commit()


@pytest.fixture
def seeded():
    product_repository.create(Product(barcode="P1", name="Widget", price=1.0, stock_quantity=1000, critical_stock_level=1))
    dealership_repository.create(Dealership("CST-04", "Harbor Point", "Coastal", "Norfolk"))
    dealership_repository.create(Dealership("MET-01", "Metro Heavy", "Metro", "Columbus"))
    _sale("2026-09-01", 100, "CST-04")
    _sale("2026-09-03", 50, "MET-01")
    _sale("2026-09-24", 25)  # no dealership: "Unassigned"
    _sale("2026-08-02", 80, "CST-04")  # previous period
    _sale("2026-08-30", 999, "CST-04")  # outside the comparison window (24 Aug)


@pytest.fixture
def page(qapp, seeded):
    from admin_app.gui.pages.reports_page import ReportsPage

    widget = ReportsPage(today_provider=lambda: TODAY)
    widget.show()
    pump(qapp)
    yield widget
    widget.close()


def test_month_view_totals_comparison_and_breakdown(page):
    view = page.current_view()
    assert view.revenue == 175.0 and view.sale_count == 3
    assert view.previous_revenue == 80.0 and view.change == 118.8
    assert page._headline.text() == "175.00"
    assert [r[0] for r in view.regions] == ["Coastal", "Metro", "Unassigned"]
    assert [d.code for d in view.top] == ["CST-04", "MET-01"]
    assert view.projected_total == round(175 / 24 * 30, 2)


def test_switching_period_and_mode_redraws_without_losing_the_data(page):
    page.set_mode("daily")
    assert page.current_view().mode == "daily" and page.current_view().current[0] == 100.0
    page.set_period("ytd")
    view = page.current_view()
    assert view.period.key == "ytd" and view.mode == "daily"
    assert view.revenue == 175.0 + 80.0 + 999.0  # every sale since 1 Jan


def test_empty_period_says_so_instead_of_drawing_a_blank(qapp, tmp_path):
    from admin_app.gui.pages.reports_page import ReportsPage

    widget = ReportsPage(today_provider=lambda: TODAY)
    pump(qapp)
    assert widget.current_view().revenue == 0.0
    assert "No sales" in widget._empty_note.text()
    widget.close()


@pytest.mark.parametrize("kind,suffix", [("csv", ".csv"), ("excel", ".xlsx"), ("pdf", ".pdf")])
def test_every_export_writes_a_file(page, tmp_path, monkeypatch, kind, suffix):
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    target = tmp_path / f"report{suffix}"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    getattr(page, f"export_{kind}")()
    assert target.exists() and target.stat().st_size > 0


# --- gross profit -----------------------------------------------------------


def _costed_sale(day_iso: str, dealership_code: str, quantity: int, cost: float, known: bool = True) -> None:
    """A sale of `quantity` x P2 (price 10) with the cost snapshot a checkout
    would have stored (cost_known 0 = sold before costs were tracked)."""
    saved = transaction_repository.finalize_transaction(
        Transaction(items=[LineItem(product_barcode="P2", product_name_at_sale="Gadget", unit_price_at_sale=10.0, quantity=quantity)])
    )
    with connection_scope() as conn:
        conn.execute(
            "UPDATE transactions SET created_at = ?, dealership_code = ? WHERE id = ?",
            (f"{day_iso}T12:00:00.000Z", dealership_code, saved.id),
        )
        conn.execute(
            "UPDATE transaction_items SET unit_cost_at_sale = ?, cost_known = ? WHERE transaction_id = ?",
            (cost, 1 if known else 0, saved.id),
        )
        conn.commit()


@pytest.fixture
def costed_world():
    product_repository.create(Product(barcode="P2", name="Gadget", price=10.0, stock_quantity=1000, critical_stock_level=1))
    dealership_repository.create(Dealership("CST-04", "Harbor Point", "Coastal", "Norfolk"))


def _open_page(qapp):
    from admin_app.gui.pages.reports_page import ReportsPage

    widget = ReportsPage(today_provider=lambda: TODAY)
    widget.show()
    pump(qapp)
    return widget


def test_profit_note_shows_profit_and_margin_when_every_cost_is_known(qapp, costed_world):
    _costed_sale("2026-09-02", "CST-04", 3, 6.0)  # revenue 30, cost 18
    widget = _open_page(qapp)
    text = widget._profit_note.text()
    assert "12.00" in text and "40.0%" in text
    assert "unknown" not in text.lower()
    widget.close()


def test_profit_note_states_how_much_revenue_has_unknown_cost(qapp, costed_world):
    _costed_sale("2026-09-02", "CST-04", 3, 6.0)  # known: revenue 30
    _costed_sale("2026-09-03", "CST-04", 1, 0.0, known=False)  # unknown: revenue 10 of 40
    widget = _open_page(qapp)
    text = widget._profit_note.text()
    assert "12.00" in text and "25%" in text
    widget.close()


def test_profit_note_has_no_profit_when_no_cost_was_ever_known(qapp, costed_world):
    _costed_sale("2026-09-02", "CST-04", 2, 0.0, known=False)
    widget = _open_page(qapp)
    assert "Gross profit" not in widget._profit_note.text()
    assert "100%" in widget._profit_note.text()
    widget.close()


def test_till_control_lists_counts_that_do_not_match_and_refunds(qapp):
    from database import account_repository as accounts
    from database import day_close_repository, employee_repository, product_repository, stock_repository
    from database import sale_return_repository
    from database import transaction_repository as sales
    from shared.auth import Actor
    from shared.models import UNASSIGNED, Employee, LineItem, Product, StockLocation, Transaction
    from admin_app.gui.pages.reports_page import ReportsPage

    dealership_repository.create(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))
    product_repository.create(Product("OIL", "Oil", 10.0, 50, 2))
    shelf = StockLocation.dealership("001")
    stock_repository.transfer(UNASSIGNED, shelf, "OIL", 10)
    employee_repository.create(Employee("B-2", "Selin Kaya", "Sales & service", "Dealership", "Harbor Point"))
    accounts.create_first_admin("B-1", "Erol Admin", "482913")
    accounts.create_account("B-2", "cashier", "5831")
    sale = sales.finalize_transaction(
        Transaction(items=[LineItem("OIL", "Oil", 10.0, 3)], payment_method="cash"), shelf, Actor("B-2", "Selin Kaya"))
    sale_return_repository.create(sale.id, [("OIL", 1, True)], "Wrong size", Actor("B-1", "Erol Admin"))
    day_close_repository.close("001", 15.0, actor=Actor("B-2", "Selin Kaya"), note="short")
    page = ReportsPage()
    try:
        assert page._closes_table.rowCount() == 1 and page._refunds_table.rowCount() == 1
        assert "-5.00" in page._closes_table.item(0, 4).text() or "5.00" in page._closes_table.item(0, 4).text()
        assert page._refunds_table.item(0, 0).text() == "RF-00001"
        assert page._refunds_table.item(0, 6).text() == "Erol Admin · B-1"
        assert "1 COUNTS OFF" in page._control_section._kicker_label.text()
    finally:
        page.close()
