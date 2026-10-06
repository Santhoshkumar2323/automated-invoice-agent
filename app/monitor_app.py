import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.config import load_config
from src.dashboard_data import (
    build_source,
    daily_usage,
    defect_breakdown,
    fmt_percent,
    ledger_table,
    ledger_total,
    overall_stats,
    run_invoice_rows,
    runs_table,
    time_ago,
)
from src.formatting import format_inr
from src.scheduler import utc_now
from src.tracking.usage import totals_for_day

STYLE = """
<style>
html { font-size: 112%; }
.block-container { max-width: 1300px; padding-top: 2rem; }
h1 { font-size: 2.4rem !important; }
div[data-testid="stMetricValue"] { font-size: 2rem; }
</style>
"""


def load_settings():
    override = os.environ.get("INVOICE_AGENT_CONFIG")
    return load_config(Path(override)) if override else load_config()


settings = load_settings()
dash = settings.dashboard
source = build_source(settings)

st.set_page_config(page_title="Invoice Agent Monitor", page_icon="🧾", layout="wide")
st.markdown(STYLE, unsafe_allow_html=True)


def collect(_source):
    return {
        "reports": _source.reports(dash.recent_runs, dash.include_dry_runs),
        "ledger": _source.ledger(),
        "usage": _source.usage_events(),
        "shots": _source.screenshots(dash.recent_screenshots),
        "errors": list(_source.errors),
    }


if dash.github_repo:
    collect = st.cache_data(ttl=dash.refresh_seconds, show_spinner="Loading from GitHub")(collect)

data = collect(source)
reports = data["reports"]
stats = overall_stats(reports)
counts = stats["counts"]

st.title("Invoice Agent: live monitor")
state = "running" if settings.pipeline.enabled else "paused"
st.caption(
    f"Read-only view | pipeline {state} | every {settings.pipeline.interval_hours:g} h | "
    f"model {settings.llm.model} | data from {source.label}"
)
if reports:
    st.caption(f"Last run: {str(reports[0].get('started_at', ''))[:16].replace('T', ' ')} UTC ({time_ago(reports[0].get('started_at', ''))})")
else:
    st.info("No runs recorded yet.")
if data["errors"]:
    st.warning("Some data could not be loaded: " + "; ".join(data["errors"][:3]))

headline = [
    ("Invoices processed", stats["invoices"]),
    ("Accepted", counts.get("ACCEPTED", 0)),
    ("Rejected", counts.get("REJECTED", 0)),
    ("Needs review", counts.get("NEEDS_REVIEW", 0)),
    ("Errors", counts.get("ERROR", 0) + counts.get("SKIPPED", 0)),
]
if dash.include_dry_runs:
    headline.insert(2, ("Dry-run validated", counts.get("VALIDATED", 0)))
for column, (label, value) in zip(st.columns(len(headline)), headline):
    column.metric(label, value)

row_two = st.columns(5)
row_two[0].metric("Extraction accuracy", fmt_percent(stats["extraction_accuracy"]))
row_two[1].metric("Bad invoices caught", f"{stats['caught']} of {stats['bad_invoices']}")
row_two[2].metric("Clean wrongly stopped", f"{stats['clean_wrongly_stopped']} of {stats['clean_invoices']}")
per_invoice = stats["tokens_per_invoice"]
row_two[3].metric(
    "Tokens per invoice",
    "n/a" if per_invoice is None else f"{per_invoice:,.0f}",
    help="Average tokens for invoices that reached the AI. Invoices that failed earlier are not counted.",
)
row_two[4].metric("Tokens spent on retries", fmt_percent(stats["retry_token_share"]))
st.caption(f"Totals cover the newest {stats['runs']} run(s)." + ("" if dash.include_dry_runs else " Dry runs are not counted."))

tab_latest, tab_ledger, tab_runs, tab_quality, tab_usage, tab_shots = st.tabs(
    ["Latest run", "Ledger", "Runs", "Quality", "Usage", "Screenshots"]
)

with tab_latest:
    if reports:
        latest = reports[0]
        mode = "dry run" if latest.get("dry_run") else "full run"
        st.subheader(f"Run {latest.get('run_id', '')} ({mode})")
        st.dataframe(
            run_invoice_rows(latest),
            hide_index=True,
            column_config={
                "File": st.column_config.TextColumn("File", width="medium"),
                "Reason": st.column_config.TextColumn("Reason", width="large"),
            },
        )
    else:
        st.info("Nothing to show yet.")

with tab_ledger:
    ledger = data["ledger"]
    left, right = st.columns(2)
    left.metric("Ledger entries", len(ledger))
    right.metric("Ledger total (INR)", format_inr(ledger_total(ledger)))
    if ledger:
        st.dataframe(ledger_table(ledger, dash.recent_ledger_rows), hide_index=True)
    else:
        st.info("The ledger is empty.")

with tab_runs:
    if reports:
        st.dataframe(runs_table(reports), hide_index=True)
    else:
        st.info("No runs yet.")

with tab_quality:
    breakdown = defect_breakdown(reports)
    if breakdown:
        st.dataframe(
            [
                {
                    "Planted problem": "none (clean invoice)" if e["defect"] == "NONE" else e["defect"],
                    "Invoices": e["invoices"],
                    "Right decision": f"{e['correct']} of {e['decided']}",
                    "Outcomes": e["outcome_text"],
                }
                for e in breakdown
            ],
            hide_index=True,
        )
    else:
        st.info("No quality data yet.")
    accuracies = [r["extraction_accuracy"] * 100 for r in reversed(reports) if r.get("extraction_accuracy") is not None]
    if len(accuracies) >= 2:
        st.line_chart({"Extraction accuracy per run (%)": accuracies})

with tab_usage:
    limit_tokens = settings.budget.daily_token_limit
    limit_pages = settings.budget.daily_page_limit
    events = data["usage"]
    tokens_today, pages_today = totals_for_day(events, utc_now().date())
    st.write(f"Tokens today (UTC): {tokens_today:,} of {'unlimited' if limit_tokens == 0 else f'{limit_tokens:,}'}")
    if limit_tokens:
        st.progress(min(tokens_today / limit_tokens, 1.0))
    st.write(f"LlamaParse pages today (UTC): {pages_today:,} of {'unlimited' if limit_pages == 0 else f'{limit_pages:,}'}")
    if limit_pages:
        st.progress(min(pages_today / limit_pages, 1.0))
    daily = daily_usage(events)
    if daily:
        st.bar_chart({"Tokens per day": [d["Tokens"] for d in daily]})
    else:
        st.info("No usage recorded yet.")

with tab_shots:
    shots = data["shots"]
    if shots:
        columns = st.columns(3)
        for index, (name, ref) in enumerate(shots):
            columns[index % 3].image(ref, caption=name)
    else:
        st.info("No screenshots available.")

st.divider()
st.caption("Everything on this page is simulated data. The invoices, vendors, tax registry and ledger are fake.")