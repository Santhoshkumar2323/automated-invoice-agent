import json
from datetime import datetime, timezone

from src.config import Settings
from src.tracking.report import (
    compare_extraction,
    judge,
    render_markdown,
    summarize_run,
    write_report,
)
from src.workflow.state import InvoiceState

START = datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc)
END = datetime(2026, 10, 2, 6, 3, tzinfo=timezone.utc)

TRUTH_INVOICE = {
    "invoice_number": "INV/2026-27/00001",
    "invoice_date": "2026-09-15",
    "vendor_name": "Alpha Labs",
    "vendor_gstin": "33AAACA7712P1ZL",
    "buyer_gstin": "33AABCM5612K1ZN",
    "line_items": [{"description": "A", "amount": 2000.0}, {"description": "B", "amount": 500.0}],
    "subtotal": 2500.0,
    "cgst": 225.0,
    "sgst": 225.0,
    "igst": 0.0,
    "total": 2950.0,
}

USAGE = {
    "llm_calls": 3,
    "prompt_tokens": 3000,
    "completion_tokens": 600,
    "total_tokens": 3600,
    "pages": 3,
    "cost_usd": 0.0,
    "tokens_by_step": {"extract": 2400, "repair": 1200},
    "retry_token_share": 0.3333,
}


def test_perfect_extraction_scores_full():
    result = compare_extraction(dict(TRUTH_INVOICE), TRUTH_INVOICE)
    assert result["correct"] == result["total"]
    assert result["wrong"] == []


def test_wrong_field_is_reported():
    extracted = dict(TRUTH_INVOICE, total=2951.5)
    result = compare_extraction(extracted, TRUTH_INVOICE)
    assert result["wrong"] == ["total"]


def test_formatting_differences_do_not_count_as_errors():
    extracted = dict(
        TRUTH_INVOICE,
        invoice_date="15 Sep 2026",
        vendor_name="  alpha   labs ",
        subtotal="Rs. 2,500.00",
        igst=None,
    )
    result = compare_extraction(extracted, TRUTH_INVOICE)
    assert result["wrong"] == []


def test_line_item_count_mismatch_is_reported():
    extracted = dict(TRUTH_INVOICE, line_items=[{"description": "A", "amount": 2000.0}])
    assert "line_items" in compare_extraction(extracted, TRUTH_INVOICE)["wrong"]


def test_non_dict_extraction_is_all_wrong():
    result = compare_extraction(None, TRUTH_INVOICE)
    assert result["correct"] == 0


def test_judge_rules():
    assert judge("ACCEPTED", [], "ACCEPT") is True
    assert judge("VALIDATED", [], "ACCEPT") is True
    assert judge("REJECTED", ["X"], "ACCEPT") is False
    assert judge("REJECTED", ["REGISTRY_SUSPENDED"], "REGISTRY_SUSPENDED") is True
    assert judge("NEEDS_REVIEW", ["ARITHMETIC_TOTAL"], "ARITHMETIC_TOTAL") is True
    assert judge("ACCEPTED", [], "REGISTRY_SUSPENDED") is False
    assert judge("REJECTED", ["OTHER"], "REGISTRY_SUSPENDED") is False
    assert judge("ERROR", [], "ACCEPT") is None
    assert judge("SKIPPED", [], "ACCEPT") is None
    assert judge("ACCEPTED", [], None) is None


def build_results():
    accepted = InvoiceState(
        file_path="a.pdf",
        file_name="a.pdf",
        status="ACCEPTED",
        reason="ledger entry verified",
        attempt=1,
        extracted=dict(TRUTH_INVOICE),
        invoice=dict(TRUTH_INVOICE),
    )
    rejected = InvoiceState(
        file_path="b.pdf",
        file_name="b.pdf",
        status="REJECTED",
        reason="REGISTRY_SUSPENDED: vendor | suspended",
        attempt=1,
        extracted=dict(TRUTH_INVOICE, vendor_name="Alpha Logistics Sandbox"),
        invoice=dict(TRUTH_INVOICE, vendor_name="Alpha Logistics Sandbox"),
        issues=[{"code": "REGISTRY_SUSPENDED", "message": "suspended", "retryable": False}],
        registry_status="SUSPENDED",
    )
    review = InvoiceState(
        file_path="c.pdf",
        file_name="c.pdf",
        status="NEEDS_REVIEW",
        reason="no progress",
        attempt=2,
        extracted=dict(TRUTH_INVOICE, total=9999.0),
        invoice=dict(TRUTH_INVOICE, total=9999.0),
        issues=[{"code": "ARITHMETIC_TOTAL", "message": "bad", "retryable": True}],
    )
    failed = InvoiceState(file_path="d.pdf", file_name="d.pdf", status="ERROR", reason="parse failed")
    return [accepted, rejected, review, failed]


def build_truth():
    return {
        "a.pdf": {"expected_outcome": "ACCEPT", "defect": "NONE", "invoice": TRUTH_INVOICE},
        "b.pdf": {
            "expected_outcome": "REGISTRY_SUSPENDED",
            "defect": "SUSPENDED_VENDOR",
            "invoice": dict(TRUTH_INVOICE, vendor_name="Alpha Logistics Sandbox"),
        },
        "c.pdf": {"expected_outcome": "ARITHMETIC_TOTAL", "defect": "WRONG_TOTAL", "invoice": TRUTH_INVOICE},
        "d.pdf": {"expected_outcome": "ACCEPT", "defect": "NONE", "invoice": TRUTH_INVOICE},
    }


def build_summary(results=None, truth=None):
    return summarize_run(
        "20261002_060000",
        START,
        END,
        Settings(),
        "interval elapsed",
        False,
        build_results() if results is None else results,
        build_truth() if truth is None else truth,
        USAGE,
        3600,
        3,
    )


def test_summary_counts_and_detection():
    summary = build_summary()
    assert summary["counts"] == {"ACCEPTED": 1, "REJECTED": 1, "NEEDS_REVIEW": 1, "ERROR": 1}
    detection = summary["detection"]
    assert detection["bad_invoices"] == 2
    assert detection["caught"] == 2
    assert detection["clean_invoices"] == 1
    assert detection["clean_wrongly_stopped"] == 0


def test_summary_extraction_accuracy_uses_only_extracted_invoices():
    summary = build_summary()
    assert 0 < summary["extraction_accuracy"] < 1
    rows = {r["file"]: r for r in summary["invoices"]}
    assert rows["a.pdf"]["extraction"]["wrong"] == []
    assert rows["c.pdf"]["extraction"]["wrong"] == ["total"]
    assert rows["d.pdf"]["extraction"] is None
    assert rows["d.pdf"]["decision_correct"] is None


def test_summary_without_ground_truth_has_no_accuracy():
    summary = build_summary(truth={})
    assert summary["extraction_accuracy"] is None
    assert all(r["decision_correct"] is None for r in summary["invoices"])


def test_markdown_contains_the_key_sections():
    text = render_markdown(build_summary())
    for heading in ("# Run report", "## Outcome", "## Invoices", "## Quality", "## Usage", "## Daily budget"):
        assert heading in text
    assert "| ACCEPTED | 1 |" in text
    assert "Bad invoices caught: 2 of 2" in text
    assert "wrong fields total" in text
    assert "Share of tokens spent on retries: 33.3%" in text


def test_markdown_escapes_pipes_in_table_cells():
    text = render_markdown(build_summary())
    assert "vendor / suspended" in text


def test_markdown_handles_empty_run():
    text = render_markdown(build_summary(results=[], truth={}))
    assert "| none | 0 |" in text
    assert "n/a" in text


def test_write_report_creates_markdown_and_json(tmp_path):
    summary = build_summary()
    md_path = write_report(tmp_path / "reports", summary)
    assert md_path.name == "run_20261002_060000.md"
    assert md_path.exists()
    loaded = json.loads(md_path.with_suffix(".json").read_text(encoding="utf-8"))
    assert loaded["counts"] == summary["counts"]