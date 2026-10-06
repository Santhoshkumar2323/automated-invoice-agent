import copy
import random
from datetime import date
from pathlib import Path

from src.config import Settings
from src.factory.generate_mock_invoices import Defect, generate_batch
from src.tracking.usage import UsageTracker
from src.workflow.graph import Deps, build_graph, process_invoice
from src.workflow.state import EntryResult, ExtractResult, ParseResult

TODAY = date(2026, 10, 2)
LEDGER = [{"vendor": "Alpha Labs", "gstin": "33AAACA7712P1ZL", "invoice_number": "INV/2026-27/00777"}]


def make_invoice(tmp_path, defect=None, ledger=None, seed=1):
    settings = Settings(pipeline={"bad_invoice_ratio": 0.0})
    record = generate_batch(
        1, settings, random.Random(seed), ledger or [], out_dir=tmp_path, today=TODAY, force_defect=defect
    )[0]
    return record["invoice"]


def with_total(invoice, delta):
    changed = copy.deepcopy(invoice)
    changed["total"] = round(changed["total"] + delta, 2)
    return changed


class Harness:
    def __init__(self, tmp_path, outputs, max_retries=2, ledger=None, budget=None, enter="ok", parse_error=None):
        self.settings = Settings(pipeline={"max_retries": max_retries})
        self.tracker = UsageTracker(tmp_path / "usage.json", self.settings.pricing, "run1")
        self.outputs = list(outputs)
        self.ledger = ledger or []
        self.budget = list(budget) if budget is not None else [None]
        self.enter_mode = enter
        self.parse_error = parse_error
        self.parse_calls = 0
        self.extract_calls = []
        self.enter_calls = []

    def parse(self, path):
        self.parse_calls += 1
        if self.parse_error:
            raise self.parse_error
        return ParseResult(markdown="# invoice markdown", pages=1)

    def extract(self, markdown, feedback, previous):
        self.extract_calls.append((feedback, previous))
        item = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        if isinstance(item, Exception):
            raise item
        return ExtractResult(data=item, prompt_tokens=1000, completion_tokens=200, model="fake-model")

    def enter(self, invoice, file_name):
        self.enter_calls.append(invoice)
        if self.enter_mode == "ok":
            return EntryResult(True, screenshot="shot.png")
        if self.enter_mode == "fail":
            return EntryResult(False, message="success token not found")
        raise RuntimeError("browser crashed")

    def budget_check(self):
        return self.budget.pop(0) if len(self.budget) > 1 else self.budget[0]

    def run(self, dry_run=False):
        deps = Deps(
            settings=self.settings,
            parse=self.parse,
            extract=self.extract,
            tracker=self.tracker,
            ledger_rows=lambda: self.ledger,
            budget_check=self.budget_check,
            enter_ledger=None if dry_run else self.enter,
        )
        return process_invoice(build_graph(deps), Path("incoming/test_invoice.pdf"))


def test_clean_invoice_is_accepted(tmp_path):
    invoice = make_invoice(tmp_path)
    h = Harness(tmp_path, [invoice])
    state = h.run()
    assert state.status == "ACCEPTED"
    assert state.attempt == 1
    assert state.ledger_confirmed is True
    assert state.screenshot == "shot.png"
    assert len(h.extract_calls) == 1
    assert len(h.enter_calls) == 1
    assert h.enter_calls[0]["vendor_gstin"] == invoice["vendor_gstin"]
    assert h.tracker.run_summary()["total_tokens"] == 1200
    assert h.tracker.run_summary()["pages"] == 1


def test_retry_with_feedback_fixes_a_bad_extraction(tmp_path):
    good = make_invoice(tmp_path)
    h = Harness(tmp_path, [with_total(good, 500), good])
    state = h.run()
    assert state.status == "ACCEPTED"
    assert state.attempt == 2
    feedback, previous = h.extract_calls[1]
    assert "ARITHMETIC_TOTAL" in feedback
    assert previous["total"] == round(good["total"] + 500, 2)
    assert h.extract_calls[0] == (None, None)
    steps = h.tracker.run_summary()["tokens_by_step"]
    assert steps == {"extract": 1200, "repair": 1200}


def test_same_output_on_retry_stops_early(tmp_path):
    bad = with_total(make_invoice(tmp_path), 500)
    h = Harness(tmp_path, [bad])
    state = h.run()
    assert state.status == "NEEDS_REVIEW"
    assert state.stalled is True
    assert state.attempt == 2
    assert len(h.extract_calls) == 2
    assert h.enter_calls == []
    assert "no progress" in state.reason


def test_retries_are_capped(tmp_path):
    good = make_invoice(tmp_path)
    outputs = [with_total(good, 100), with_total(good, 200), with_total(good, 300), with_total(good, 400)]
    h = Harness(tmp_path, outputs, max_retries=2)
    state = h.run()
    assert state.status == "NEEDS_REVIEW"
    assert state.attempt == 3
    assert len(h.extract_calls) == 3
    assert "3 attempt" in state.reason


def test_zero_retries_means_one_attempt(tmp_path):
    bad = with_total(make_invoice(tmp_path), 500)
    h = Harness(tmp_path, [bad], max_retries=0)
    state = h.run()
    assert state.status == "NEEDS_REVIEW"
    assert len(h.extract_calls) == 1


def test_suspended_vendor_is_rejected_without_retry(tmp_path):
    invoice = make_invoice(tmp_path, Defect.SUSPENDED_VENDOR)
    h = Harness(tmp_path, [invoice])
    state = h.run()
    assert state.status == "REJECTED"
    assert state.registry_status == "SUSPENDED"
    assert state.attempt == 1
    assert len(h.extract_calls) == 1
    assert h.enter_calls == []
    assert "REGISTRY_SUSPENDED" in state.reason


def test_revoked_and_unknown_vendors_are_rejected(tmp_path):
    for defect, code in ((Defect.REVOKED_VENDOR, "REGISTRY_REVOKED"), (Defect.UNKNOWN_VENDOR, "REGISTRY_NOT_FOUND")):
        h = Harness(tmp_path, [make_invoice(tmp_path, defect)])
        state = h.run()
        assert state.status == "REJECTED"
        assert code in state.reason
        assert h.enter_calls == []


def test_duplicate_invoice_is_rejected(tmp_path):
    invoice = make_invoice(tmp_path, Defect.DUPLICATE_INVOICE, ledger=LEDGER)
    h = Harness(tmp_path, [invoice], ledger=LEDGER)
    state = h.run()
    assert state.status == "REJECTED"
    assert "DUPLICATE_INVOICE" in state.reason
    assert len(h.extract_calls) == 1


def test_wrong_total_on_the_pdf_ends_in_review_not_ledger(tmp_path):
    invoice = make_invoice(tmp_path, Defect.WRONG_TOTAL)
    h = Harness(tmp_path, [invoice])
    state = h.run()
    assert state.status == "NEEDS_REVIEW"
    assert "ARITHMETIC_TOTAL" in state.reason
    assert h.enter_calls == []


def test_bad_checksum_ends_in_review(tmp_path):
    invoice = make_invoice(tmp_path, Defect.BAD_CHECKSUM)
    h = Harness(tmp_path, [invoice])
    state = h.run()
    assert state.status == "NEEDS_REVIEW"
    assert "GSTIN_CHECKSUM" in state.reason


def test_parse_failure_is_an_error(tmp_path):
    h = Harness(tmp_path, [make_invoice(tmp_path)], parse_error=RuntimeError("402 payment required"))
    state = h.run()
    assert state.status == "ERROR"
    assert "parse failed" in state.reason
    assert h.extract_calls == []
    assert h.tracker.run_summary()["pages"] == 0


def test_extraction_failure_is_an_error(tmp_path):
    h = Harness(tmp_path, [RuntimeError("rate limited")])
    state = h.run()
    assert state.status == "ERROR"
    assert "extraction failed" in state.reason
    assert "rate limited" in state.reason


def test_ledger_not_confirmed_is_an_error(tmp_path):
    h = Harness(tmp_path, [make_invoice(tmp_path)], enter="fail")
    state = h.run()
    assert state.status == "ERROR"
    assert state.ledger_confirmed is False
    assert "success token not found" in state.reason


def test_browser_crash_is_an_error(tmp_path):
    h = Harness(tmp_path, [make_invoice(tmp_path)], enter="crash")
    state = h.run()
    assert state.status == "ERROR"
    assert "browser crashed" in state.reason


def test_budget_gate_skips_before_any_work(tmp_path):
    h = Harness(tmp_path, [make_invoice(tmp_path)], budget=["daily token budget reached (10/10)"])
    state = h.run()
    assert state.status == "SKIPPED"
    assert h.parse_calls == 0
    assert h.extract_calls == []
    assert h.tracker.events == []


def test_budget_reached_during_retry_skips_the_retry(tmp_path):
    good = make_invoice(tmp_path)
    h = Harness(tmp_path, [with_total(good, 500), good], budget=[None, "daily token budget reached (1/1)"])
    state = h.run()
    assert state.status == "SKIPPED"
    assert "during retry" in state.reason
    assert len(h.extract_calls) == 1


def test_dry_run_validates_but_does_not_enter(tmp_path):
    h = Harness(tmp_path, [make_invoice(tmp_path)])
    state = h.run(dry_run=True)
    assert state.status == "VALIDATED"
    assert state.ledger_confirmed is False
    assert h.enter_calls == []


def test_empty_extraction_is_a_schema_failure(tmp_path):
    h = Harness(tmp_path, [{}])
    state = h.run()
    assert state.status == "NEEDS_REVIEW"
    assert [i["code"] for i in state.issues] == ["SCHEMA"]
    assert h.enter_calls == []


def test_messy_but_correct_extraction_is_normalised_and_accepted(tmp_path):
    invoice = make_invoice(tmp_path)
    messy = copy.deepcopy(invoice)
    year, month, day = messy["invoice_date"].split("-")
    messy["invoice_date"] = f"{day}/{month}/{year}"
    messy["vendor_gstin"] = messy["vendor_gstin"].lower()
    for key in ("subtotal", "cgst", "sgst", "igst", "total"):
        messy[key] = f"Rs. {messy[key]:,.2f}"
    for item in messy["line_items"]:
        item["rate"] = f"{item['rate']:,.2f}"
        item["amount"] = f"{item['amount']:,.2f}"
    h = Harness(tmp_path, [messy])
    state = h.run()
    assert state.status == "ACCEPTED", state.reason
    assert h.enter_calls[0]["invoice_date"] == invoice["invoice_date"]
    assert h.enter_calls[0]["vendor_gstin"] == invoice["vendor_gstin"]


def test_trace_records_the_path_taken(tmp_path):
    good = make_invoice(tmp_path)
    h = Harness(tmp_path, [with_total(good, 500), good])
    state = h.run()
    joined = " | ".join(state.trace)
    assert "budget ok" in joined
    assert "repair attempt 2" in joined
    assert "ledger entry verified" in joined