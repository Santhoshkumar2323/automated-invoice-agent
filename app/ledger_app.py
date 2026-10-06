import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.config import load_config
from src.formatting import format_inr
from src.ledger import SUCCESS_TOKEN, LedgerError, add_entry, read_rows

STYLE = """
<style>
html { font-size: 118%; }
.block-container { max-width: 1100px; padding-top: 2rem; }
h1 { font-size: 2.6rem !important; }
div[data-testid="stTextInput"] label p { font-size: 1.05rem; font-weight: 600; }
div[data-testid="stTextInput"] input { font-size: 1.1rem; min-height: 3rem; }
div[data-testid="stFormSubmitButton"] button { font-size: 1.15rem; padding: 0.6rem 2rem; }
div[data-testid="stMetricValue"] { font-size: 2.4rem; }
</style>
"""


def ledger_path() -> Path:
    override = os.environ.get("LEDGER_FILE")
    if override:
        return Path(override)
    return load_config().path("ledger_file")


st.set_page_config(page_title="Accounts Ledger", page_icon="🧾", layout="wide")
st.markdown(STYLE, unsafe_allow_html=True)

path = ledger_path()

st.title("Accounts Ledger")
st.caption("Meridian Retail Pvt Ltd | internal accounts payable system")

with st.form("ledger_form", clear_on_submit=True):
    first_left, first_right = st.columns(2)
    vendor = first_left.text_input("Vendor Name", key="vendor_name")
    gstin = first_right.text_input("GSTIN", key="vendor_gstin")
    second_left, second_right = st.columns(2)
    invoice_number = second_left.text_input("Invoice Number", key="invoice_number")
    amount = second_right.text_input("Amount (INR)", key="amount_inr")
    submitted = st.form_submit_button("Submit Ledger Entry")

if submitted:
    try:
        row = add_entry(path, vendor, gstin, invoice_number, amount)
        st.success(f"✨ {SUCCESS_TOKEN}")
        st.caption(f"{row['vendor']} | {row['invoice_number']} | INR {format_inr(row['amount'])}")
    except LedgerError as exc:
        st.error(f"Entry rejected: {exc}")

rows = read_rows(path)
st.divider()
left, right = st.columns(2)
left.metric("Entries", len(rows))
right.metric("Total (INR)", format_inr(sum(float(r.get("amount", 0) or 0) for r in rows)))
if rows:
    table = [
        {
            "Timestamp": r.get("timestamp", ""),
            "Vendor": r.get("vendor", ""),
            "GSTIN": r.get("gstin", ""),
            "Invoice Number": r.get("invoice_number", ""),
            "Amount": format_inr(float(r.get("amount", 0) or 0)),
            "Status": r.get("status", ""),
        }
        for r in reversed(rows)
    ]
    st.dataframe(table, hide_index=True)
else:
    st.info("No entries yet.")