"""Regression tests for pos_app.services.checkout_service.

TODO (next slice):
- test_complete_sale_calls_print_after_successful_write
- test_complete_sale_does_not_print_on_insufficient_stock: guards
  against printing a receipt for a sale that failed to persist
"""


def test_complete_sale_takes_stock_off_the_given_shelf(tmp_path, monkeypatch):
    import database.connection as connection
    from database import dealership_repository, product_repository, stock_repository
    from pos_app.services import checkout_service
    from shared.models import UNASSIGNED, Dealership, LineItem, Product, StockLocation, Transaction

    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "shelf.db")
    monkeypatch.setattr(connection, "_initialized", False)
    monkeypatch.setattr(checkout_service, "print_receipt", lambda receipt: None)
    dealership_repository.create(Dealership(code="001", name="Harbor", region="Coastal", city="X"))
    product_repository.create(Product("BOX", "Carton", 40, 10, 1))
    shelf = StockLocation.dealership("001")
    stock_repository.transfer(UNASSIGNED, shelf, "BOX", 4)

    done = checkout_service.complete_sale(Transaction(items=[LineItem("BOX", "Carton", 40, 3)]), shelf)

    assert done.dealership_code == "001"
    assert stock_repository.quantity_at(shelf, "BOX") == 1
    assert stock_repository.quantity_at(UNASSIGNED, "BOX") == 6
