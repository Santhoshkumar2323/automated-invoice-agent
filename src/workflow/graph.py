from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from src.config import Settings
from src.tracking.usage import UsageTracker
from src.validation.grounding import issues_confirmed_by_source
from src.validation.models import Invoice
from src.validation.validators import validate_invoice
from src.workflow.state import EntryResult, ExtractResult, InvoiceState, ParseResult

ParseFn = Callable[[Path], ParseResult]
ExtractFn = Callable[[str, Optional[str], Optional[dict]], ExtractResult]
EntryFn = Callable[[dict, str], EntryResult]


@dataclass
class Deps:
    settings: Settings
    parse: ParseFn
    extract: ExtractFn
    tracker: UsageTracker
    ledger_rows: Callable[[], list[dict]]
    budget_check: Callable[[], Optional[str]]
    enter_ledger: Optional[EntryFn] = None


def _issue_text(issues: list[dict]) -> str:
    return "\n".join(f"- {i['code']}: {i['message']}" for i in issues)


def _issue_summary(issues: list[dict]) -> str:
    return "; ".join(f"{i['code']}: {i['message']}" for i in issues)


def _short_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]]
        return "; ".join(parts)
    return str(exc)


def build_graph(deps: Deps):
    max_retries = deps.settings.pipeline.max_retries

    def source_confirms(state: InvoiceState) -> bool:
        if not deps.settings.pipeline.skip_pointless_retries:
            return False
        return issues_confirmed_by_source(state.issues, state.invoice or {}, state.markdown)

    def check_budget(state: InvoiceState) -> dict:
        reason = deps.budget_check()
        if reason:
            return {"status": "SKIPPED", "reason": reason, "trace": state.trace + [f"skipped: {reason}"]}
        return {"trace": state.trace + ["budget ok"]}

    def parse_node(state: InvoiceState) -> dict:
        try:
            result = deps.parse(Path(state.file_path))
        except Exception as exc:
            return {
                "status": "ERROR",
                "reason": f"parse failed: {exc}",
                "trace": state.trace + ["parse failed"],
            }
        deps.tracker.record_parse(result.pages, invoice=state.file_name)
        return {"markdown": result.markdown, "trace": state.trace + [f"parsed {result.pages} page(s)"]}

    def extract_node(state: InvoiceState) -> dict:
        retrying = state.attempt > 0
        if retrying:
            reason = deps.budget_check()
            if reason:
                return {
                    "status": "SKIPPED",
                    "reason": f"{reason} (during retry)",
                    "trace": state.trace + ["skipped retry: budget"],
                }
        feedback = _issue_text(state.issues) if retrying else None
        previous = state.extracted if retrying else None
        try:
            result = deps.extract(state.markdown, feedback, previous)
        except Exception as exc:
            return {
                "status": "ERROR",
                "reason": f"extraction failed: {exc}",
                "trace": state.trace + ["extraction failed"],
            }
        step = "repair" if retrying else "extract"
        deps.tracker.record_llm(
            step, result.model, result.prompt_tokens, result.completion_tokens, invoice=state.file_name
        )
        stalled = retrying and result.data == state.extracted
        return {
            "extracted": result.data,
            "attempt": state.attempt + 1,
            "stalled": stalled,
            "trace": state.trace + [f"{step} attempt {state.attempt + 1}" + (" (no change)" if stalled else "")],
        }

    def validate_node(state: InvoiceState) -> dict:
        try:
            invoice = Invoice(**(state.extracted or {}))
        except (ValidationError, TypeError, ValueError) as exc:
            issue = {"code": "SCHEMA", "message": _short_error(exc), "retryable": True}
            return {
                "invoice": None,
                "issues": [issue],
                "registry_status": None,
                "trace": state.trace + ["schema check failed"],
            }
        result = validate_invoice(invoice, deps.settings.checks, deps.ledger_rows())
        issues = [{"code": i.code, "message": i.message, "retryable": i.retryable} for i in result.issues]
        return {
            "invoice": invoice.model_dump(mode="json"),
            "issues": issues,
            "registry_status": result.registry_status,
            "trace": state.trace + ["validation passed" if not issues else f"validation found {len(issues)} issue(s)"],
        }

    def enter_node(state: InvoiceState) -> dict:
        if deps.enter_ledger is None:
            return {
                "status": "VALIDATED",
                "reason": "all checks passed; ledger entry skipped (dry run)",
                "trace": state.trace + ["dry run: no ledger entry"],
            }
        try:
            result = deps.enter_ledger(state.invoice or {}, state.file_name)
        except Exception as exc:
            return {
                "status": "ERROR",
                "reason": f"ledger entry failed: {exc}",
                "trace": state.trace + ["ledger entry failed"],
            }
        if result.success:
            return {
                "status": "ACCEPTED",
                "reason": "ledger entry verified",
                "ledger_confirmed": True,
                "screenshot": result.screenshot,
                "trace": state.trace + ["ledger entry verified"],
            }
        return {
            "status": "ERROR",
            "reason": f"ledger entry failed: {result.message}",
            "screenshot": result.screenshot,
            "trace": state.trace + ["ledger entry not confirmed"],
        }

    def reject_node(state: InvoiceState) -> dict:
        return {
            "status": "REJECTED",
            "reason": _issue_summary(state.issues),
            "trace": state.trace + ["rejected"],
        }

    def review_node(state: InvoiceState) -> dict:
        if state.stalled:
            reason = "retry produced the same output, so no progress was possible; " + _issue_summary(state.issues)
        elif state.attempt <= max_retries and source_confirms(state):
            reason = "the invoice itself prints these values, so a retry could not help; " + _issue_summary(state.issues)
        else:
            reason = f"still failing after {state.attempt} attempt(s); " + _issue_summary(state.issues)
        return {"status": "NEEDS_REVIEW", "reason": reason, "trace": state.trace + ["needs human review"]}

    def after_budget(state: InvoiceState) -> str:
        return END if state.status == "SKIPPED" else "parse"

    def after_parse(state: InvoiceState) -> str:
        return END if state.status == "ERROR" else "extract"

    def after_extract(state: InvoiceState) -> str:
        if state.status in ("ERROR", "SKIPPED"):
            return END
        if state.stalled:
            return "needs_review"
        return "validate"

    def after_validate(state: InvoiceState) -> str:
        if not state.issues:
            return "enter"
        if not all(i["retryable"] for i in state.issues):
            return "reject"
        if state.attempt <= max_retries:
            return "needs_review" if source_confirms(state) else "extract"
        return "needs_review"

    graph = StateGraph(InvoiceState)
    graph.add_node("check_budget", check_budget)
    graph.add_node("parse", parse_node)
    graph.add_node("extract", extract_node)
    graph.add_node("validate", validate_node)
    graph.add_node("enter", enter_node)
    graph.add_node("reject", reject_node)
    graph.add_node("needs_review", review_node)

    graph.add_edge(START, "check_budget")
    graph.add_conditional_edges("check_budget", after_budget, {END: END, "parse": "parse"})
    graph.add_conditional_edges("parse", after_parse, {END: END, "extract": "extract"})
    graph.add_conditional_edges(
        "extract", after_extract, {END: END, "needs_review": "needs_review", "validate": "validate"}
    )
    graph.add_conditional_edges(
        "validate",
        after_validate,
        {"enter": "enter", "reject": "reject", "extract": "extract", "needs_review": "needs_review"},
    )
    graph.add_edge("enter", END)
    graph.add_edge("reject", END)
    graph.add_edge("needs_review", END)
    return graph.compile()


def process_invoice(graph, file_path: Path) -> InvoiceState:
    initial = InvoiceState(file_path=str(file_path), file_name=Path(file_path).name)
    result = graph.invoke(initial, config={"recursion_limit": 50})
    return result if isinstance(result, InvoiceState) else InvoiceState(**result)