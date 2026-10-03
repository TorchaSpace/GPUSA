"""Offscreen GUI tests for the Reports page / legacy sales tab fixes."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from tests.gui_support import pump, qapp  # noqa: F401  (qapp is a fixture)

import database.connection as connection
from database import dealership_repository, product_repository, transaction_repository
from database.connection import connection_scope
from shared.builders.report_builder import ReportDocument
from shared.models import Dealership, LineItem, Product, Transaction


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _sale(day_iso: str, amount: float, dealership_code: str | None = None) -> None:
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
    dealership_repository.create(Dealership("MET-01", "Metro Heavy", "Metro", "Columbus"))
    start = date(2026, 9, 29)
    for i in range(7):  # steady 100 a day, 29 Sep - 5 Oct
        _sale((start + timedelta(days=i)).isoformat(), 100, "MET-01")


def test_seven_day_trend_early_in_a_month_uses_the_days_before_it(qapp, seeded):
    from admin_app.gui.pages.reports_page import ReportsPage

    page = ReportsPage(today_provider=lambda: date(2026, 10, 5))
    pump(qapp)
    view = page.current_view()
    assert view.revenue == 500.0  # October only
    assert view.top[0].week == [100.0] * 7 and view.top[0].trend == 0.0
    page.close()


def test_toggling_the_chart_mode_keeps_the_period_captured_at_reload(qapp, seeded):
    from admin_app.gui.pages.reports_page import ReportsPage

    clock = {"today": date(2026, 10, 5)}
    page = ReportsPage(today_provider=lambda: clock["today"])
    pump(qapp)
    clock["today"] = date(2026, 10, 6)  # midnight passes while the page is open
    page.set_mode("daily")
    assert page.current_view().period.end == date(2026, 10, 5)  # same window as the data and the export
    page.reload()
    assert page.current_view().period.end == date(2026, 10, 6)
    page.close()


def test_sales_tab_rejects_a_start_after_the_end(qapp, monkeypatch):
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QMessageBox

    from admin_app.gui.sales_reports_tab import SalesReportsTab

    tab = SalesReportsTab()
    tab._start_input.setDate(QDate(2026, 10, 5))
    tab._end_input.setDate(QDate(2026, 10, 1))
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))

    def _boom(*a, **k):
        raise AssertionError("must not query for an inverted range")

    monkeypatch.setattr(transaction_repository, "list_between", _boom)
    tab._generate()
    assert len(warned) == 1 and tab._current_report is None
    tab.close()


def test_sales_tab_title_shows_the_inclusive_end_date(qapp):
    from PySide6.QtCore import QDate

    from admin_app.gui.sales_reports_tab import SalesReportsTab

    tab = SalesReportsTab()
    tab._start_input.setDate(QDate(2026, 10, 1))
    tab._end_input.setDate(QDate(2026, 10, 3))
    tab._generate()
    assert tab._current_report.title == "Sales Report: 2026-10-01 to 2026-10-03"
    tab.close()


def test_sales_tab_export_appends_the_extension_instead_of_replacing_a_dotted_name(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from admin_app.gui.sales_reports_tab import SalesReportsTab

    tab = SalesReportsTab()
    tab._current_report = ReportDocument("T", datetime(2026, 10, 4))
    typed = tmp_path / "Report 2026.10.03"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(typed), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    written: list[Path] = []
    tab._export(lambda report, path: written.append(path), "PDF (*.pdf)", ".pdf")
    assert written == [tmp_path / "Report 2026.10.03.pdf"]

    # an existing file is only replaced after confirmation
    (tmp_path / "Report 2026.10.03.pdf").write_text("old")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    tab._export(lambda report, path: written.append(path), "PDF (*.pdf)", ".pdf")
    assert len(written) == 1
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    tab._export(lambda report, path: written.append(path), "PDF (*.pdf)", ".pdf")
    assert len(written) == 2
    tab.close()


def test_sales_tab_reports_a_database_error_from_the_exporter(qapp, tmp_path, monkeypatch):
    import sqlite3

    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from admin_app.gui.sales_reports_tab import SalesReportsTab

    tab = SalesReportsTab()
    tab._current_report = ReportDocument("T", datetime(2026, 10, 4))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "r.pdf"), ""))
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))

    def _fail(report, path):
        raise sqlite3.OperationalError("disk I/O error")

    tab._export(_fail, "PDF (*.pdf)", ".pdf")
    assert len(warned) == 1
    tab.close()
