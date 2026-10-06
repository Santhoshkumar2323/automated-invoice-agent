import json
import random
from datetime import date

from src.config import Settings
from src.factory.generate_mock_invoices import (
    BAD_DEFECTS,
    Defect,
    append_ground_truth,
    generate_batch,
    inr,
)
from src.validation.models import Invoice
from src.validation.validators import validate_invoice

TODAY = date(2026, 10, 2)

LEDGER = [
    {"vendor": "Alpha Labs", "gstin": "33AAACA7712P1ZL", "invoice_number": "INV/2026-27/00777"},
    {"vendor": "Quantum Tech Labs", "gstin": "27AABCQ4821M1Z7", "invoice_number": "QTL-2026-0042"},
]


def settings(ratio):
    return Settings(pipeline={"bad_invoice_ratio": ratio})


def run(tmp_path, count, ratio, seed, ledger=None, force=None):
    return generate_batch(
        count,
        settings(ratio),
        random.Random(seed),
        ledger if ledger is not None else [],
        out_dir=tmp_path,
        today=TODAY,
        force_defect=force,
    )


def test_pdf_files_are_created(tmp_path):
    records = run(tmp_path, 5, 0.0, 1)
    assert len(records) == 5
    for record in records:
        data = (tmp_path / record["file"]).read_bytes()
        assert data.startswith(b"%PDF")
        assert len(data) > 1000


def test_ratio_zero_means_all_clean(tmp_path):
    records = run(tmp_path, 30, 0.0, 2)
    assert {r["defect"] for r in records} == {"NONE"}
    assert {r["expected_outcome"] for r in records} == {"ACCEPT"}


def test_ratio_one_means_all_defective(tmp_path):
    records = run(tmp_path, 30, 1.0, 3, ledger=LEDGER)
    assert "NONE" not in {r["defect"] for r in records}


def test_every_defect_type_appears(tmp_path):
    records = run(tmp_path, 120, 1.0, 4, ledger=LEDGER)
    assert {r["defect"] for r in records} == {d.value for d in BAD_DEFECTS}


def test_duplicate_falls_back_without_ledger(tmp_path):
    records = run(tmp_path, 3, 1.0, 5, ledger=[], force=Defect.DUPLICATE_INVOICE)
    assert {r["defect"] for r in records} == {"WRONG_TOTAL"}


def test_same_seed_gives_same_invoices(tmp_path):
    a = run(tmp_path / "a", 8, 0.5, 11, ledger=LEDGER)
    b = run(tmp_path / "b", 8, 0.5, 11, ledger=LEDGER)
    assert [r["invoice"] for r in a] == [r["invoice"] for r in b]
    assert [r["defect"] for r in a] == [r["defect"] for r in b]


def test_validators_agree_with_ground_truth(tmp_path):
    settings_obj = settings(0.6)
    for seed in range(1, 9):
        records = generate_batch(
            25,
            settings_obj,
            random.Random(seed),
            LEDGER,
            out_dir=tmp_path / str(seed),
            today=TODAY,
        )
        for record in records:
            invoice = Invoice(**record["invoice"])
            result = validate_invoice(invoice, settings_obj.checks, LEDGER)
            if record["expected_outcome"] == "ACCEPT":
                assert result.passed, (record["defect"], result.summary())
            else:
                assert record["expected_outcome"] in result.codes, (record["defect"], result.summary())
                assert not result.passed


def test_clean_invoices_use_valid_tax_split(tmp_path):
    for record in run(tmp_path, 40, 0.0, 6):
        inv = record["invoice"]
        if inv["vendor_gstin"][:2] == inv["buyer_gstin"][:2]:
            assert inv["igst"] == 0 and inv["cgst"] == inv["sgst"] > 0
        else:
            assert inv["cgst"] == 0 and inv["sgst"] == 0 and inv["igst"] > 0


def test_ground_truth_append_roundtrip(tmp_path):
    path = tmp_path / "truth.json"
    append_ground_truth(path, [{"file": "a.pdf"}])
    append_ground_truth(path, [{"file": "b.pdf"}])
    assert [r["file"] for r in json.loads(path.read_text(encoding="utf-8"))] == ["a.pdf", "b.pdf"]


def test_ground_truth_recovers_from_corrupt_file(tmp_path):
    path = tmp_path / "truth.json"
    path.write_text("{broken", encoding="utf-8")
    append_ground_truth(path, [{"file": "a.pdf"}])
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 1


def test_indian_number_format():
    assert inr(209600.2) == "2,09,600.20"
    assert inr(999.5) == "999.50"
    assert inr(1000) == "1,000.00"
    assert inr(12345678.9) == "1,23,45,678.90"