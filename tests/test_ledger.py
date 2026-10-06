import json
from datetime import datetime, timezone

import pytest

from src.ledger import SUCCESS_TOKEN, LedgerError, add_entry, read_rows

NOW = datetime(2026, 10, 2, 14, 30, 0, tzinfo=timezone.utc)
GSTIN = "33AAACA7712P1ZL"
OTHER_GSTIN = "27AABCQ4821M1Z7"
   
def add(path, vendor="Alpha Labs", gstin=GSTIN, number="INV-1", amount="1000", now=NOW):
    return add_entry(path, vendor, gstin, number, amount, now=now)


def test_entry_is_saved_with_all_fields(tmp_path):
    path = tmp_path / "ledger.json"
    row = add(path)
    assert row == {
        "timestamp": "2026-10-02T14:30:00+00:00",
        "vendor": "Alpha Labs",
        "gstin": GSTIN,
        "invoice_number": "INV-1",
        "amount": 1000.0,
        "status": "VERIFIED",
    }
    assert json.loads(path.read_text(encoding="utf-8")) == [row]


def test_parent_folders_are_created(tmp_path):
    path = tmp_path / "deep" / "folder" / "ledger.json"
    add(path)
    assert path.exists()


def test_inputs_are_cleaned(tmp_path):
    path = tmp_path / "ledger.json"
    row = add_entry(path, "  Alpha Labs  ", " 33aaaca7712p1zl ", "  INV-9 ", "Rs. 2,09,600.20", now=NOW)
    assert row["vendor"] == "Alpha Labs"
    assert row["gstin"] == GSTIN
    assert row["invoice_number"] == "INV-9"
    assert row["amount"] == 209600.20


def test_numeric_amount_is_accepted(tmp_path):
    assert add(tmp_path / "ledger.json", amount=1500.5)["amount"] == 1500.5


def test_entries_accumulate_in_order(tmp_path):
    path = tmp_path / "ledger.json"
    add(path, number="A")
    add(path, number="B")
    add(path, number="C")
    assert [r["invoice_number"] for r in read_rows(path)] == ["A", "B", "C"]


def test_no_temp_file_is_left_behind(tmp_path):
    path = tmp_path / "ledger.json"
    add(path)
    assert [p.name for p in tmp_path.iterdir()] == ["ledger.json"]


@pytest.mark.parametrize(
    "kwargs,fragment",
    [
        ({"vendor": ""}, "vendor name"),
        ({"gstin": "  "}, "GSTIN"),
        ({"number": ""}, "invoice number"),
        ({"amount": ""}, "amount"),
        ({"amount": None}, "amount"),
    ],
)
def test_missing_fields_are_rejected(tmp_path, kwargs, fragment):
    with pytest.raises(LedgerError) as exc:
        add(tmp_path / "ledger.json", **kwargs)
    assert exc.value.code == "MISSING_FIELD"
    assert fragment in str(exc.value)


def test_all_missing_fields_are_listed(tmp_path):
    with pytest.raises(LedgerError) as exc:
        add_entry(tmp_path / "ledger.json", "", "", "", "")
    assert "vendor name, GSTIN, invoice number, amount" in str(exc.value)


def test_bad_gstin_is_rejected(tmp_path):
    for bad in ("1234", GSTIN[:-1] + ("A" if GSTIN[-1] != "A" else "B")):
        with pytest.raises(LedgerError) as exc:
            add(tmp_path / "ledger.json", gstin=bad)
        assert exc.value.code == "BAD_GSTIN"
    assert not (tmp_path / "ledger.json").exists()


@pytest.mark.parametrize("bad", ["abc", "0", "-5", "0.00", "nan"])
def test_bad_amounts_are_rejected(tmp_path, bad):
    with pytest.raises(LedgerError) as exc:
        add(tmp_path / "ledger.json", amount=bad)
    assert exc.value.code == "BAD_AMOUNT"


def test_duplicate_is_rejected_and_ledger_unchanged(tmp_path):
    path = tmp_path / "ledger.json"
    add(path)
    before = path.read_text(encoding="utf-8")
    with pytest.raises(LedgerError) as exc:
        add(path, amount="999")
    assert exc.value.code == "DUPLICATE"
    assert path.read_text(encoding="utf-8") == before


def test_same_number_from_another_vendor_is_allowed(tmp_path):
    path = tmp_path / "ledger.json"
    add(path)
    add(path, vendor="Quantum Tech Labs", gstin=OTHER_GSTIN)
    assert len(read_rows(path)) == 2


def test_corrupt_ledger_is_never_overwritten(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(LedgerError) as exc:
        add(path)
    assert exc.value.code == "CORRUPT"
    assert path.read_text(encoding="utf-8") == "{broken"


def test_wrong_shaped_ledger_is_never_overwritten(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text('{"rows": []}', encoding="utf-8")
    with pytest.raises(LedgerError) as exc:
        add(path)
    assert exc.value.code == "CORRUPT"


def test_read_rows_on_missing_file_is_empty(tmp_path):
    assert read_rows(tmp_path / "none.json") == []


def test_success_token_is_stable():
    assert SUCCESS_TOKEN == "Ledger Entry Verified"



def test_empty_ledger_file_is_treated_as_a_fresh_ledger(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text("", encoding="utf-8")
    add(path)
    assert len(read_rows(path)) == 1


def test_whitespace_only_ledger_file_is_treated_as_a_fresh_ledger(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text("  \n", encoding="utf-8")
    add(path)
    assert len(read_rows(path)) == 1


def test_ledger_file_with_a_byte_order_mark_is_readable(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_bytes(b"\xef\xbb\xbf[]")
    add(path)
    assert len(read_rows(path)) == 1


def test_utf16_ledger_file_is_refused_not_overwritten(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text("[]", encoding="utf-16")
    before = path.read_bytes()
    with pytest.raises(LedgerError) as exc:
        add(path)
    assert exc.value.code == "CORRUPT"
    assert path.read_bytes() == before


def test_corrupt_message_says_what_is_wrong(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(LedgerError) as exc:
        add(path)
    assert "not valid JSON" in str(exc.value)    