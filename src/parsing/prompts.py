from __future__ import annotations

import json

EXAMPLE_MARKDOWN = """# TAX INVOICE

| Sample Traders Pvt Ltd Plot 1, Example Road Chennai, Tamil Nadu - 600001 GSTIN: 33AAAAA0000A1Z5 | Invoice No: SMP/2026-27/00001 Invoice Date: 05 Mar 2026 Place of Supply: Karnataka (29) |
|---|---|

| Bill To Example Buyer Ltd GSTIN: 29BBBBB1111B1Z6 |
|---|

| Sr | Description | HSN/SAC | Qty | Rate (Rs.) | Amount (Rs.) |
|---|---|---|---|---|---|
| 1 | Sample widget | 1234 | 2 | 1,500.00 | 3,000.00 |
| 2 | Sample service | 9987 | 1 | 12,500.50 | 12,500.50 |

| Subtotal | 15,500.50 |
|---|---|
| IGST @ 18% | 2,790.09 |
| Total | 18,290.59 |"""

EXAMPLE_INVOICE = {
    "invoice_number": "SMP/2026-27/00001",
    "invoice_date": "2026-03-05",
    "vendor_name": "Sample Traders Pvt Ltd",
    "vendor_gstin": "33AAAAA0000A1Z5",
    "buyer_name": "Example Buyer Ltd",
    "buyer_gstin": "29BBBBB1111B1Z6",
    "place_of_supply": "Karnataka (29)",
    "line_items": [
        {"description": "Sample widget", "hsn_code": "1234", "quantity": 2, "rate": 1500.0, "amount": 3000.0},
        {"description": "Sample service", "hsn_code": "9987", "quantity": 1, "rate": 12500.5, "amount": 12500.5},
    ],
    "subtotal": 15500.5,
    "cgst": 0,
    "sgst": 0,
    "igst": 2790.09,
    "total": 18290.59,
}

SYSTEM_PROMPT = f"""You are an invoice data extraction engine for Indian GST tax invoices.
You receive the text of one invoice, converted to Markdown, and you return its contents as a single JSON object.

Rules:
1. Copy values exactly as they are printed on the invoice. Never correct, recalculate, round, guess or invent a value. If the invoice prints a wrong total, return that wrong total.
2. Return only the JSON object, with no explanation and no code fences.
3. Money amounts and quantities are plain numbers with no currency symbol and no thousands separators. Write 209600.20, never "Rs. 2,09,600.20".
4. Dates on Indian invoices are day first. Return invoice_date as YYYY-MM-DD.
5. A GSTIN has 15 characters (uppercase letters and digits, no spaces). vendor_gstin belongs to the seller who issued the invoice. buyer_gstin belongs to the Bill To party.
6. If a tax line is not printed on the invoice, use 0 for it. If any other value is not printed, use an empty string.
7. List every line item, in the order printed.

The JSON object has exactly these fields:
{{
  "invoice_number": string,
  "invoice_date": "YYYY-MM-DD",
  "vendor_name": string,
  "vendor_gstin": string,
  "buyer_name": string,
  "buyer_gstin": string,
  "place_of_supply": string,
  "line_items": [{{"description": string, "hsn_code": string, "quantity": number, "rate": number, "amount": number}}],
  "subtotal": number,
  "cgst": number,
  "sgst": number,
  "igst": number,
  "total": number
}}

Example input:
{EXAMPLE_MARKDOWN}

Example output:
{json.dumps(EXAMPLE_INVOICE, indent=2)}"""


def build_extract_messages(markdown: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Extract the invoice below into the JSON object.\n\nINVOICE (Markdown):\n{markdown}"},
    ]


def build_repair_messages(markdown: str, previous: dict, feedback: str) -> list[dict]:
    previous_json = json.dumps(previous, indent=2, default=str)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Your previous extraction of this invoice was checked and these problems were found:\n"
                f"{feedback}\n\n"
                f"Your previous extraction was:\n{previous_json}\n\n"
                "Read the invoice again. If you misread or mis-copied a value, fix it. "
                "If the invoice itself prints exactly these values, return the previous extraction unchanged. "
                "Never change a printed value just to make the arithmetic work.\n\n"
                f"INVOICE (Markdown):\n{markdown}"
            ),
        },
    ]