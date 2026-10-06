import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parent.parent / "app" / "ledger_app.py"
GSTIN = "33AAACA7712P1ZL"


@pytest.fixture
def ledger_file(tmp_path, monkeypatch):
    path = tmp_path / "ledger.json"
    monkeypatch.setenv("LEDGER_FILE", str(path))
    return path


def open_app():
    return AppTest.from_file(str(APP), default_timeout=30).run()


def submit(at, vendor="Alpha Labs", gstin=GSTIN, number="INV-1", amount="1,000.50"):
    at.text_input(key="vendor_name").input(vendor)
    at.text_input(key="vendor_gstin").input(gstin)
    at.text_input(key="invoice_number").input(number)
    at.text_input(key="amount_inr").input(amount)
    at.button[0].click().run()
    return at


def test_app_loads_with_an_empty_ledger(ledger_file):
    at = open_app()
    assert not at.exception
    assert [t.label for t in at.text_input] == ["Vendor Name", "GSTIN", "Invoice Number", "Amount (INR)"]
    assert at.button[0].label == "Submit Ledger Entry"
    assert at.info[0].value == "No entries yet."
    assert at.metric[0].value == "0"


def test_valid_entry_shows_the_success_message_and_is_saved(ledger_file):
    at = submit(open_app())
    assert not at.exception
    assert "Ledger Entry Verified" in at.success[0].value
    rows = json.loads(ledger_file.read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["vendor"] == "Alpha Labs"
    assert rows[0]["amount"] == 1000.5
    assert rows[0]["status"] == "VERIFIED"
    assert at.metric[0].value == "1"


def test_invalid_gstin_is_rejected_and_nothing_is_saved(ledger_file):
    at = submit(open_app(), gstin="BAD")
    assert not at.success
    assert "Entry rejected" in at.error[0].value
    assert not ledger_file.exists()


def test_duplicate_entry_is_rejected(ledger_file):
    at = submit(open_app())
    at = submit(at)
    assert not at.success
    assert "already exists" in at.error[0].value
    assert len(json.loads(ledger_file.read_text(encoding="utf-8"))) == 1


def test_empty_form_reports_missing_fields(ledger_file):
    at = open_app()
    at.button[0].click().run()
    assert "missing" in at.error[0].value
    assert not ledger_file.exists()


def test_existing_rows_are_shown_when_the_app_opens(ledger_file):
    ledger_file.write_text(
        json.dumps(
            [
                {"timestamp": "t", "vendor": "A", "gstin": GSTIN, "invoice_number": "1", "amount": 100.0, "status": "VERIFIED"},
                {"timestamp": "t", "vendor": "B", "gstin": GSTIN, "invoice_number": "2", "amount": 250.5, "status": "VERIFIED"},
            ]
        ),
        encoding="utf-8",
    )
    at = open_app()
    assert at.metric[0].value == "2"
    assert at.metric[1].value == "350.50"
    assert len(at.dataframe) == 1




def test_empty_ledger_file_does_not_block_the_first_entry(ledger_file):
    ledger_file.write_text("", encoding="utf-8")
    at = submit(open_app())
    assert "Ledger Entry Verified" in at.success[0].value
    assert len(json.loads(ledger_file.read_text(encoding="utf-8"))) == 1



def test_amounts_are_shown_with_indian_grouping(ledger_file):
    ledger_file.write_text(
        json.dumps(
            [{"timestamp": "t", "vendor": "A", "gstin": GSTIN, "invoice_number": "1", "amount": 141536.98, "status": "VERIFIED"}]
        ),
        encoding="utf-8",
    )
    at = open_app()
    assert at.metric[1].value == "1,41,536.98"
    table = at.dataframe[0].value
    assert list(table["Amount"]) == ["1,41,536.98"]
    assert list(table.columns) == ["Timestamp", "Vendor", "GSTIN", "Invoice Number", "Amount", "Status"]


def test_confirmation_caption_uses_indian_grouping(ledger_file):
    at = submit(open_app(), amount="2,09,600.20")
    assert any("INR 2,09,600.20" in caption.value for caption in at.caption)    