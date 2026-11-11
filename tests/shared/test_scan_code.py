"""shared.scan_code: what a reader typed -> the product codes worth looking up."""

from shared.scan_code import candidates


def test_a_plain_barcode_is_itself():
    assert candidates("  8691234510531 ") == ["8691234510531"]
    assert candidates("") == [] and candidates(None) == []


def test_gs1_digital_link_qr_gives_the_gtin_and_its_ean13():
    assert candidates("https://id.gs1.org/01/08691234510531/10/LOT7?x=1") == [
        "https://id.gs1.org/01/08691234510531/10/LOT7?x=1", "08691234510531", "8691234510531"]


def test_gs1_element_strings_with_and_without_brackets():
    assert candidates("(01)08691234510531(17)271231")[1:] == ["08691234510531", "8691234510531"]
    assert candidates("010869123451053117271231")[1:] == ["08691234510531", "8691234510531"]


def test_a_symbology_prefix_is_dropped():
    assert candidates("]C1SKU-001") == ["]C1SKU-001", "SKU-001"]


def test_short_numbers_starting_with_01_are_left_alone():
    assert candidates("0123456789012") == ["0123456789012"]
