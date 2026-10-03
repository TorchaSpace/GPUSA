"""shared.overview - the numbers behind Admin's Overview cards and stock grid."""

import pytest

from shared import overview
from shared.models import Dealership, Product, StockLevel, StockLocation, Warehouse

WH1 = Warehouse("WH-01", "Duluth", capacity_units=1000)
WH2 = Warehouse("WH-02", "Memphis", capacity_units=500)
WH_OFF = Warehouse("WH-09", "Old", capacity_units=100, is_active=False)
NO_CAP = Warehouse("WH-03", "New")


def _level(loc, barcode, qty):
    return StockLevel(loc, barcode, barcode, qty)


def test_capacity_summary_overall_and_near_capacity():
    used = {WH1.location: 900, WH2.location: 100, WH_OFF.location: 50}
    s = overview.capacity_summary([WH1, WH2, WH_OFF], used)
    assert (s.active, s.inactive) == (2, 1)
    assert s.overall == pytest.approx(1000 / 1500)
    assert s.near == ["WH-01"]
    assert [w.code for w in s.warehouses] == ["WH-01", "WH-02"]
    assert s.warehouses[0].fraction == 0.9 and s.warehouses[1].fraction == 0.2


def test_capacity_summary_without_any_capacity_has_no_overall():
    s = overview.capacity_summary([NO_CAP], {NO_CAP.location: 40})
    assert s.overall is None and s.near == [] and s.warehouses[0].fraction is None
    assert overview.capacity_summary([], {}).overall is None


def test_dealership_summary_counts_active_selling_and_regions():
    ds = [Dealership("A", "A", "Metro", "x"), Dealership("B", "B", "Metro", "x"),
          Dealership("C", "C", "Coastal", "x"), Dealership("D", "D", "Valley", "x", is_active=False)]
    s = overview.dealership_summary(ds, {"A", "C", "D"})
    assert (s.active, s.inactive, s.selling, s.silent) == (3, 1, 2, 1)
    assert s.by_region == {"Metro": 2, "Coastal": 1}


def _products():
    return [
        Product("P1", "Pump", 10, stock_quantity=30, critical_stock_level=5),
        Product("P2", "Hose", 5, stock_quantity=0, critical_stock_level=5),
        Product("P3", "Belt", 5, stock_quantity=4, critical_stock_level=5),
    ]


def _levels():
    return [
        _level(WH1.location, "P1", 10), _level(WH2.location, "P1", 8),
        _level(StockLocation.dealership("CST-04"), "P1", 5), _level(StockLocation.dealership("MET-01"), "P1", 2),
        _level(StockLocation("unassigned"), "P3", 4),
    ]


def test_grid_all_locations_columns_cells_and_on_the_road():
    g = overview.stock_grid(_products(), _levels(), [WH1, WH2, WH_OFF])
    assert g.columns == ["WH-01", "WH-02", "Dealerships", "Unassigned", "On the road"]
    pump, hose, belt = g.rows
    assert pump.cells == [10, 8, 7, 0, 5]  # 30 total, 25 placed, 5 on a truck
    assert pump.total == 30 and pump.status == overview.STATUS_IN
    assert hose.cells == [0, 0, 0, 0, 0] and hose.status == overview.STATUS_OUT
    assert belt.cells == [0, 0, 0, 4, 0] and belt.status == overview.STATUS_LOW


def test_grid_hides_the_road_column_when_nothing_is_on_a_truck():
    products = [Product("P1", "Pump", 10, stock_quantity=10, critical_stock_level=1)]
    g = overview.stock_grid(products, [_level(WH1.location, "P1", 10)], [WH1])
    assert g.columns == ["WH-01", "Dealerships", "Unassigned"]


def test_grid_filters_keep_columns_but_status_stays_network_wide():
    wh = overview.stock_grid(_products(), _levels(), [WH1, WH2], overview.FILTER_WAREHOUSES)
    assert wh.columns == ["WH-01", "WH-02"] and wh.rows[0].total == 18 and wh.rows[0].network_total == 30
    dl = overview.stock_grid(_products(), _levels(), [WH1, WH2], overview.FILTER_DEALERSHIPS)
    assert dl.columns == ["Dealerships"] and dl.rows[0].total == 7
    assert dl.rows[2].status == overview.STATUS_LOW  # Belt: nothing at dealers, but judged on the network


def test_grid_below_reorder_and_search_filters():
    g = overview.stock_grid(_products(), _levels(), [WH1], below_reorder_only=True)
    assert [r.barcode for r in g.rows] == ["P2", "P3"]
    g = overview.stock_grid(_products(), _levels(), [WH1], query="  hos ")
    assert [r.barcode for r in g.rows] == ["P2"]
    assert overview.stock_grid(_products(), _levels(), [WH1], query="zzz").rows == []


def test_units_at_a_deactivated_warehouse_are_not_lost_from_the_totals():
    products = [Product("P1", "Pump", 10, stock_quantity=10, critical_stock_level=1)]
    g = overview.stock_grid(products, [_level(WH_OFF.location, "P1", 10)], [WH1, WH_OFF])
    assert g.columns == ["WH-01", "Dealerships", "Unassigned"]
    assert g.rows[0].cells == [0, 0, 10] and g.rows[0].total == 10


def test_grid_rejects_an_unknown_filter():
    with pytest.raises(ValueError):
        overview.stock_grid([], [], [], "everywhere")


def test_product_status():
    assert overview.product_status(Product("A", "a", 1, 0, 0)) == overview.STATUS_OUT
    assert overview.product_status(Product("A", "a", 1, 5, 5)) == overview.STATUS_LOW
    assert overview.product_status(Product("A", "a", 1, 6, 5)) == overview.STATUS_IN


def test_attendance_today_counts_and_orders_by_latest_check_in():
    roster = [
        {"name": "A", "status": "Present", "is_active": True, "check_in_at": "2026-09-24T06:00:00.000Z"},
        {"name": "B", "status": "Checked out", "is_active": True, "check_in_at": "2026-09-24T05:00:00.000Z"},
        {"name": "C", "status": "Off", "is_active": True, "check_in_at": None},
        {"name": "D", "status": "Present", "is_active": False, "check_in_at": "2026-09-24T07:00:00.000Z"},
        {"name": "E", "status": "Present", "is_active": True, "check_in_at": "2026-09-24T08:00:00.000Z"},
    ]
    a = overview.attendance_today(roster)
    assert (a.on_site, a.checked_out, a.not_in) == (2, 1, 1)
    assert [e["name"] for e in a.rows] == ["E", "A", "B"]
    assert overview.attendance_today([]).rows == []
