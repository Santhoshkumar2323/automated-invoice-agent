from datetime import date

import pytest
from pydantic import ValidationError

from src.config import CheckSettings
from src.factory.gst_data import BUYER
from src.validation import gst_registry
from src.validation.models import Invoice, parse_amount, parse_date
from src.validation.validators import (
    GSTIN_PATTERN,
    STATE_CODES,
    build_gstin,
    check_arithmetic,
    check_duplicate,
    check_gstin,
    check_registry,
    gstin_check_char,
    load_ledger_rows,
    validate_invoice,
)

CHECKS = CheckSettings()
ACTIVE_INTRA = "33AAACA7712P1ZL"
ACTIVE_INTER = "27AABCQ4821M1Z7"


def make_invoice(vendor=ACTIVE_INTRA, **overrides):
    intra = vendor[:2] == BUYER["gstin"][:2]
    data = {
        "invoice_number": "INV/2026-27/00001",
        "invoice_date": "2026-09-15",
        "vendor_name": "Test Vendor",
        "vendor_gstin": vendor,
        "buyer_name": BUYER["name"],
        "buyer_gstin": BUYER["gstin"],
        "place_of_supply": "Tamil Nadu (33)",
        "line_items": [
            {"description": "Item A", "hsn_code": "8471", "quantity": 2, "rate": 1000.0, "amount": 2000.0},
            {"description": "Item B", "hsn_code": "9983", "quantity": 1, "rate": 500.0, "amount": 500.0},
        ],
        "subtotal": 2500.0,
        "cgst": 225.0 if intra else 0.0,
        "sgst": 225.0 if intra else 0.0,
        "igst": 0.0 if intra else 450.0,
        "total": 2950.0,
    }
    data.update(overrides)
    return Invoice(**data)


def test_checksum_matches_published_sample_gstin():
    assert gstin_check_char("27AAPFU0939F1Z") == "V"


def test_every_registry_gstin_is_well_formed():
    for gstin in gst_registry.REGISTRY:
        assert GSTIN_PATTERN.match(gstin)
        assert gstin[:2] in STATE_CODES
        assert gstin_check_char(gstin[:14]) == gstin[14]


def test_buyer_gstin_is_well_formed():
    assert gstin_check_char(BUYER["gstin"][:14]) == BUYER["gstin"][14]


def test_build_gstin_roundtrip():
    gstin = build_gstin("27", "AABCX1234Y")
    assert check_gstin(gstin, CHECKS) == []


def test_valid_gstin_passes():
    assert check_gstin(ACTIVE_INTRA, CHECKS) == []


def test_bad_format_flagged():
    issues = check_gstin("1234", CHECKS)
    assert issues[0].code == "GSTIN_FORMAT"
    assert issues[0].retryable is True


def test_bad_checksum_flagged():
    broken = ACTIVE_INTRA[:-1] + ("A" if ACTIVE_INTRA[-1] != "A" else "B")
    assert [i.code for i in check_gstin(broken, CHECKS)] == ["GSTIN_CHECKSUM"]


def test_unknown_state_code_flagged():
    gstin = build_gstin("50", "AABCX1234Y")
    assert "GSTIN_STATE" in [i.code for i in check_gstin(gstin, CHECKS)]


def test_disabled_checks_are_skipped():
    off = CheckSettings(gstin_format=False, gstin_checksum=False, state_code=False)
    broken = ACTIVE_INTRA[:-1] + ("A" if ACTIVE_INTRA[-1] != "A" else "B")
    assert check_gstin(broken, off) == []
    assert check_gstin("1234", off) == []


def test_good_intra_state_invoice_passes_arithmetic():
    assert check_arithmetic(make_invoice()) == []


def test_good_inter_state_invoice_passes_arithmetic():
    assert check_arithmetic(make_invoice(vendor=ACTIVE_INTER)) == []


def test_wrong_total_flagged():
    codes = [i.code for i in check_arithmetic(make_invoice(total=3100.0))]
    assert codes == ["ARITHMETIC_TOTAL"]


def test_wrong_subtotal_flagged():
    codes = [i.code for i in check_arithmetic(make_invoice(subtotal=2600.0, total=3050.0))]
    assert "ARITHMETIC_SUBTOTAL" in codes


def test_wrong_line_amount_flagged():
    items = [{"description": "A", "hsn_code": "1", "quantity": 2, "rate": 1000.0, "amount": 1500.0}]
    codes = [i.code for i in check_arithmetic(make_invoice(line_items=items, subtotal=1500.0, cgst=135.0, sgst=135.0, total=1770.0))]
    assert "ARITHMETIC_LINE" in codes


def test_igst_on_same_state_flagged():
    inv = make_invoice(cgst=0.0, sgst=0.0, igst=450.0)
    assert "TAX_TYPE_MISMATCH" in [i.code for i in check_arithmetic(inv)]


def test_cgst_on_interstate_flagged():
    inv = make_invoice(vendor=ACTIVE_INTER, cgst=225.0, sgst=225.0, igst=0.0)
    assert "TAX_TYPE_MISMATCH" in [i.code for i in check_arithmetic(inv)]


def test_duplicate_detected_only_for_same_vendor_and_number():
    inv = make_invoice()
    rows = [{"gstin": ACTIVE_INTRA, "invoice_number": "INV/2026-27/00001"}]
    assert [i.code for i in check_duplicate(inv, rows)] == ["DUPLICATE_INVOICE"]
    other_vendor = [{"gstin": ACTIVE_INTER, "invoice_number": "INV/2026-27/00001"}]
    assert check_duplicate(inv, other_vendor) == []
    other_number = [{"gstin": ACTIVE_INTRA, "invoice_number": "INV/2026-27/00002"}]
    assert check_duplicate(inv, other_number) == []


@pytest.mark.parametrize(
    "gstin,status",
    [
        ("33AAACA7712P1ZL", "ACTIVE"),
        ("33AABCA1234F1ZG", "SUSPENDED"),
        ("24AABCP7765J1Z3", "REVOKED"),
        (build_gstin("27", "AABCX1234Y"), "NOT_FOUND"),
    ],
)
def test_registry_statuses(gstin, status):
    found, issues = check_registry(gstin)
    assert found == status
    assert (issues == []) == (status == "ACTIVE")
    for issue in issues:
        assert issue.retryable is False


def test_full_validation_passes_for_good_invoice():
    result = validate_invoice(make_invoice(), CHECKS, [])
    assert result.passed
    assert result.registry_status == "ACTIVE"
    assert result.should_retry is False


def test_suspended_vendor_is_rejected_without_retry():
    result = validate_invoice(make_invoice(vendor="33AABCA1234F1ZG"), CHECKS, [])
    assert result.codes == ["REGISTRY_SUSPENDED"]
    assert result.should_retry is False


def test_extraction_style_error_is_retryable():
    result = validate_invoice(make_invoice(total=9999.0), CHECKS, [])
    assert result.codes == ["ARITHMETIC_TOTAL"]
    assert result.should_retry is True


def test_mixed_issues_are_not_retryable():
    rows = [{"gstin": ACTIVE_INTRA, "invoice_number": "INV/2026-27/00001"}]
    result = validate_invoice(make_invoice(total=9999.0), CHECKS, rows)
    assert set(result.codes) == {"ARITHMETIC_TOTAL", "DUPLICATE_INVOICE"}
    assert result.should_retry is False


def test_registry_skipped_when_gstin_itself_is_invalid():
    broken = ACTIVE_INTRA[:-1] + ("A" if ACTIVE_INTRA[-1] != "A" else "B")
    result = validate_invoice(make_invoice(vendor=broken), CHECKS, [])
    assert "GSTIN_CHECKSUM" in result.codes
    assert result.registry_status is None


def test_registry_check_can_be_disabled():
    off = CheckSettings(registry_status=False)
    result = validate_invoice(make_invoice(vendor="33AABCA1234F1ZG"), off, [])
    assert result.passed


def test_load_ledger_rows_handles_missing_and_corrupt(tmp_path):
    assert load_ledger_rows(tmp_path / "missing.json") == []
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    assert load_ledger_rows(bad) == []
    ok = tmp_path / "ok.json"
    ok.write_text('[{"gstin": "x"}, 5]', encoding="utf-8")
    assert load_ledger_rows(ok) == [{"gstin": "x"}]


@pytest.mark.parametrize(
    "text",
    ["2026-10-02", "02-10-2026", "02/10/2026", "02.10.2026", "02 Oct 2026", "02 October 2026", "Oct 02, 2026", "October 2, 2026"],
)
def test_date_formats_parse(text):
    assert parse_date(text) == date(2026, 10, 2)


def test_bad_date_rejected():
    with pytest.raises(ValueError):
        parse_date("not a date")


@pytest.mark.parametrize(
    "text,value",
    [("2,09,600.20", 209600.20), ("Rs. 1,500", 1500.0), ("₹ 2,500.50", 2500.5), ("INR 99", 99.0), (12, 12.0)],
)
def test_amount_parsing(text, value):
    assert parse_amount(text) == value


def test_nan_and_empty_amounts_rejected():
    with pytest.raises(ValueError):
        parse_amount("nan")
    with pytest.raises(ValueError):
        parse_amount("")


def test_model_normalises_gstin_and_strings():
    inv = make_invoice(vendor_gstin=" 33aaaca7712p1zl ", invoice_date="02/10/2026")
    assert inv.vendor_gstin == "33AAACA7712P1ZL"
    assert inv.invoice_date == date(2026, 10, 2)


def test_model_requires_line_items():
    with pytest.raises(ValidationError):
        make_invoice(line_items=[])


def test_model_rejects_negative_total():
    with pytest.raises(ValidationError):
        make_invoice(total=-5)