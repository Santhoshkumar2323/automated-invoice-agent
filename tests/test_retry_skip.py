import copy
import random
from datetime import date
from pathlib import Path

from src.config import Settings
from src.factory.generate_mock_invoices import Defect, generate_batch, inr
from src.tracking.usage import UsageTracker
from src.workflow.graph import Deps, build_graph, process_invoice
from src.workflow.state import EntryResult, ExtractResult, ParseResult

TODAY = date(2026, 10, 2)


def make_invoice(tmp_path, defect=None, seed=1):
    settings = Settings(pipeline={"bad_invoice_ratio": 0.0})
    record = generate_batch(1, settings, random.Random(seed), [], out_dir=tmp_path, today=TODAY, force_defect=defect)[0]
    return record["invoice"]


def render_markdown(invoice):
    rows = [f"| Vendor | GSTIN: {invoice['vendor_gstin']} | Invoice No: {invoice['invoice_number']} |"]
    for item in invoice["line_items"]:
        rows.append(
            f"| {item['description']} | {item['hsn_code']} | {item['quantity']:g} | {inr(item['rate'])} | {inr(item['amount'])} |"
        )
    rows.append(f"| Subtotal | {inr(invoice['subtotal'])} |")
    if invoice["igst"] > 0:
        rows.append(f"| IGST @ 18% | {inr(invoice['igst'])} |")
    else:
        rows.append(f"| CGST @ 9% | {inr(invoice['cgst'])} |")
        rows.append(f"| SGST @ 9% | {inr(invoice['sgst'])} |")
    rows.append(f"| Total | {inr(invoice['total'])} |")
    return "\n".join(rows)


class Harness:
    def __init__(self, tmp_path, markdown, outputs, skip=True):
        self.settings = Settings(pipeline={"skip_pointless_retries": skip})
        self.tracker = UsageTracker(tmp_path / "usage.json", self.settings.pricing, "run1")
        self.markdown = markdown
        self.outputs = list(outputs)
        self.extract_calls = 0
        self.enter_calls = 0

    def parse(self, path):
        return ParseResult(markdown=self.markdown, pages=1)

    def extract(self, markdown, feedback, previous):
        self.extract_calls += 1
        item = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        return ExtractResult(data=item, prompt_tokens=1000, completion_tokens=200, model="fake")

    def enter(self, invoice, file_name):
        self.enter_calls += 1
        return EntryResult(True, screenshot="shot.png")

    def run(self):
        deps = Deps(
            settings=self.settings,
            parse=self.parse,
            extract=self.extract,
            tracker=self.tracker,
            ledger_rows=lambda: [],
            budget_check=lambda: None,
            enter_ledger=self.enter,
        )
        return process_invoice(build_graph(deps), Path("incoming/test.pdf"))


def test_wrong_total_printed_on_the_invoice_skips_the_retry(tmp_path):
    invoice = make_invoice(tmp_path, Defect.WRONG_TOTAL)
    h = Harness(tmp_path, render_markdown(invoice), [invoice])
    state = h.run()
    assert state.status == "NEEDS_REVIEW"
    assert state.attempt == 1
    assert h.extract_calls == 1
    assert h.enter_calls == 0
    assert "invoice itself prints these values" in state.reason
    assert "ARITHMETIC_TOTAL" in state.reason
    assert "repair" not in h.tracker.run_summary()["tokens_by_step"]


def test_bad_checksum_printed_on_the_invoice_skips_the_retry(tmp_path):
    invoice = make_invoice(tmp_path, Defect.BAD_CHECKSUM)
    h = Harness(tmp_path, render_markdown(invoice), [invoice])
    state = h.run()
    assert state.status == "NEEDS_REVIEW"
    assert h.extract_calls == 1
    assert "GSTIN_CHECKSUM" in state.reason


def test_misread_value_still_gets_its_retry(tmp_path):
    good = make_invoice(tmp_path)
    misread = copy.deepcopy(good)
    misread["total"] = round(good["total"] + 500, 2)
    h = Harness(tmp_path, render_markdown(good), [misread, good])
    state = h.run()
    assert state.status == "ACCEPTED"
    assert state.attempt == 2
    assert h.extract_calls == 2


def test_misread_gstin_still_gets_its_retry(tmp_path):
    good = make_invoice(tmp_path)
    misread = copy.deepcopy(good)
    misread["vendor_gstin"] = good["vendor_gstin"][:-1] + ("A" if good["vendor_gstin"][-1] != "A" else "B")
    h = Harness(tmp_path, render_markdown(good), [misread, good])
    state = h.run()
    assert state.status == "ACCEPTED"
    assert h.extract_calls == 2


def test_switching_the_feature_off_restores_the_old_behaviour(tmp_path):
    invoice = make_invoice(tmp_path, Defect.WRONG_TOTAL)
    h = Harness(tmp_path, render_markdown(invoice), [invoice], skip=False)
    state = h.run()
    assert state.status == "NEEDS_REVIEW"
    assert state.attempt == 2
    assert state.stalled is True
    assert h.extract_calls == 2
    assert "no progress" in state.reason


def test_the_saving_is_one_whole_repair_call(tmp_path):
    invoice = make_invoice(tmp_path, Defect.WRONG_TOTAL)
    with_skip = Harness(tmp_path, render_markdown(invoice), [invoice], skip=True)
    with_skip.run()
    without = Harness(tmp_path, render_markdown(invoice), [invoice], skip=False)
    without.run()
    saved = without.tracker.run_summary()["total_tokens"] - with_skip.tracker.run_summary()["total_tokens"]
    assert saved == 1200