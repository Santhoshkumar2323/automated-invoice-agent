import pytest

from src.factory.generate_mock_invoices import inr
from src.formatting import format_inr
from src.validation.grounding import _present, issues_confirmed_by_source

GSTIN = "33AAACA7712P1ZL"

INVOICE = {
    "vendor_gstin": GSTIN,
    "line_items": [
        {"quantity": 10, "rate": 1500.5, "amount": 15005.0},
        {"quantity": 2, "rate": 3000.0, "amount": 6000.0},
    ],
    "subtotal": 21005.0,
    "cgst": 1890.45,
    "sgst": 1890.45,
    "igst": 0.0,
    "total": 24785.9,
}

TEXT = """
| Alpha Labs GSTIN: 33AAACA7712P1ZL |
| 1 | Widget | 8471 | 10 | 1,500.50 | 15,005.00 |
| 2 | Service | 9983 | 2 | 3,000.00 | 6,000.00 |
| Subtotal | 21,005.00 |
| CGST @ 9% | 1,890.45 |
| SGST @ 9% | 1,890.45 |
| Total | 24,785.90 |
"""


def issue(code):
    return {"code": code, "message": "x", "retryable": True}


@pytest.mark.parametrize(
    "value,text",
    [
        (209600.2, "Total 2,09,600.20"),
        (209600.2, "Total 209600.20"),
        (209600.2, "Total 209,600.20"),
        (1500.0, "Qty 2 Rate 1,500.00"),
        (1500.0, "Rate 1500"),
        (10.0, "| 10 |"),
        (59000.0, "Rs.59,000.00"),
        (59000.0, "Rs. 59,000.00."),
    ],
)
def test_amounts_are_found_in_common_print_formats(value, text):
    assert _present(value, text)


@pytest.mark.parametrize(
    "value,text",
    [
        (100.0, "Total 1000.00"),
        (100.0, "Total 100.50"),
        (100.0, "Total 5,100.00"),
        (1500.0, "Total 11,500.00"),
        (5.0, "Total 15.00"),
        (209600.2, "Total 209600.25"),
    ],
)
def test_partial_number_matches_are_not_accepted(value, text):
    assert not _present(value, text)


def test_wrong_total_printed_on_the_invoice_is_confirmed():
    invoice = dict(INVOICE, total=26000.0)
    text = TEXT.replace("24,785.90", "26,000.00")
    assert issues_confirmed_by_source([issue("ARITHMETIC_TOTAL")], invoice, text) is True


def test_misread_total_is_not_confirmed():
    invoice = dict(INVOICE, total=26000.0)
    assert issues_confirmed_by_source([issue("ARITHMETIC_TOTAL")], invoice, TEXT) is False


def test_one_misread_tax_amount_blocks_the_shortcut():
    invoice = dict(INVOICE, cgst=1899.99)
    assert issues_confirmed_by_source([issue("ARITHMETIC_TOTAL")], invoice, TEXT) is False


def test_gstin_printed_exactly_is_confirmed_even_with_spacing_and_case():
    assert issues_confirmed_by_source([issue("GSTIN_CHECKSUM")], INVOICE, "gstin: 33AAACA7712 P1ZL") is True


def test_gstin_not_in_text_is_not_confirmed():
    assert issues_confirmed_by_source([issue("GSTIN_CHECKSUM")], dict(INVOICE, vendor_gstin="33AAACA7712P1ZX"), TEXT) is False


def test_subtotal_and_line_issues_check_their_values():
    assert issues_confirmed_by_source([issue("ARITHMETIC_SUBTOTAL")], INVOICE, TEXT) is True
    assert issues_confirmed_by_source([issue("ARITHMETIC_LINE")], INVOICE, TEXT) is True
    bad_line = dict(INVOICE, line_items=[{"quantity": 10, "rate": 1500.5, "amount": 15999.0}])
    assert issues_confirmed_by_source([issue("ARITHMETIC_LINE")], bad_line, TEXT) is False


def test_tax_type_issue_checks_tax_amounts():
    assert issues_confirmed_by_source([issue("TAX_TYPE_MISMATCH")], INVOICE, TEXT) is True


def test_zero_values_are_ignored():
    only_zero = {"vendor_gstin": GSTIN, "line_items": [], "subtotal": 0, "cgst": 0, "sgst": 0, "igst": 0, "total": 0}
    assert issues_confirmed_by_source([issue("TAX_TYPE_MISMATCH")], only_zero, TEXT) is False


def test_unknown_or_schema_issues_never_skip_a_retry():
    assert issues_confirmed_by_source([issue("SCHEMA")], INVOICE, TEXT) is False
    assert issues_confirmed_by_source([issue("ARITHMETIC_TOTAL"), issue("SCHEMA")], INVOICE, TEXT) is False


def test_missing_inputs_never_skip_a_retry():
    assert issues_confirmed_by_source([], INVOICE, TEXT) is False
    assert issues_confirmed_by_source([issue("ARITHMETIC_TOTAL")], {}, TEXT) is False
    assert issues_confirmed_by_source([issue("ARITHMETIC_TOTAL")], INVOICE, "") is False


@pytest.mark.parametrize("value", [0, 5, 999.5, 1000, 12345.67, 100000, 1234567.89, 12345678.9, -2500.5])
def test_indian_formatter_matches_the_pdf_generator(value):
    assert format_inr(value) == inr(value)


def test_indian_formatter_examples():
    assert format_inr(209600.2) == "2,09,600.20"
    assert format_inr(999.5) == "999.50"
    assert format_inr(1000) == "1,000.00"
    assert format_inr(12345678.9) == "1,23,45,678.90"