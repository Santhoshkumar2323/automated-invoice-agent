from __future__ import annotations

import argparse
import json
import random
import re
from datetime import date, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from src.config import Settings, load_config
from src.factory import gst_data
from src.validation import gst_registry
from src.validation.validators import CHARSET, STATE_CODES, build_gstin, load_ledger_rows


class Defect(str, Enum):
    NONE = "NONE"
    BAD_CHECKSUM = "BAD_CHECKSUM"
    SUSPENDED_VENDOR = "SUSPENDED_VENDOR"
    REVOKED_VENDOR = "REVOKED_VENDOR"
    UNKNOWN_VENDOR = "UNKNOWN_VENDOR"
    WRONG_TOTAL = "WRONG_TOTAL"
    DUPLICATE_INVOICE = "DUPLICATE_INVOICE"


EXPECTED_OUTCOME = {
    Defect.NONE: "ACCEPT",
    Defect.BAD_CHECKSUM: "GSTIN_CHECKSUM",
    Defect.SUSPENDED_VENDOR: "REGISTRY_SUSPENDED",
    Defect.REVOKED_VENDOR: "REGISTRY_REVOKED",
    Defect.UNKNOWN_VENDOR: "REGISTRY_NOT_FOUND",
    Defect.WRONG_TOTAL: "ARITHMETIC_TOTAL",
    Defect.DUPLICATE_INVOICE: "DUPLICATE_INVOICE",
}

BAD_DEFECTS = [d for d in Defect if d is not Defect.NONE]


def pick_defect(rng: random.Random, ratio: float, has_ledger: bool) -> Defect:
    if rng.random() >= ratio:
        return Defect.NONE
    pool = [d for d in BAD_DEFECTS if has_ledger or d is not Defect.DUPLICATE_INVOICE]
    return rng.choice(pool)


def _invoice_number(rng: random.Random, vendor_name: str, day: date) -> str:
    style = rng.randint(0, 2)
    if style == 0:
        return f"INV/{gst_data.financial_year(day)}/{rng.randint(1, 99999):05d}"
    abbr = "".join(word[0] for word in vendor_name.split()[:3]).upper()
    if style == 1:
        return f"{abbr}-{day.year}-{rng.randint(1, 9999):04d}"
    return f"{abbr}{rng.randint(1, 999999):06d}"


def _pick_vendor(rng: random.Random, defect: Defect, ledger_rows: list[dict]) -> tuple[str, str, str]:
    if defect is Defect.DUPLICATE_INVOICE:
        row = rng.choice(ledger_rows)
        record = gst_registry.lookup(row["gstin"]) or {}
        return record.get("name", row.get("vendor", "Unknown Vendor")), row["gstin"], record.get("city", "Chennai")

    if defect is Defect.SUSPENDED_VENDOR:
        gstin = rng.choice(gst_registry.vendors_with_status("SUSPENDED"))
    elif defect is Defect.REVOKED_VENDOR:
        gstin = rng.choice(gst_registry.vendors_with_status("REVOKED"))
    elif defect is Defect.UNKNOWN_VENDOR:
        while True:
            state = rng.choice(list(gst_data.STATE_CITIES))
            gstin = build_gstin(state, gst_data.random_pan(rng))
            if gst_registry.lookup(gstin) is None:
                break
        return rng.choice(gst_data.UNKNOWN_VENDOR_NAMES), gstin, gst_data.STATE_CITIES[state]
    else:
        gstin = rng.choice(gst_registry.vendors_with_status("ACTIVE"))

    record = gst_registry.REGISTRY[gstin]
    return record["name"], gstin, record["city"]


def build_invoice(
    rng: random.Random,
    defect: Defect,
    ledger_rows: list[dict],
    used: set[tuple[str, str]],
    today: date,
) -> tuple[dict, dict, Defect]:
    if defect is Defect.DUPLICATE_INVOICE and not ledger_rows:
        defect = Defect.WRONG_TOTAL

    vendor_name, gstin, city = _pick_vendor(rng, defect, ledger_rows)

    if defect is Defect.BAD_CHECKSUM:
        wrong = rng.choice([c for c in CHARSET if c != gstin[-1]])
        gstin = gstin[:-1] + wrong

    day = today - timedelta(days=rng.randint(0, 45))

    if defect is Defect.DUPLICATE_INVOICE:
        number = rng.choice([r["invoice_number"] for r in ledger_rows if r["gstin"] == gstin])
    else:
        number = _invoice_number(rng, vendor_name, day)
        while (gstin, number) in used:
            number = _invoice_number(rng, vendor_name, day)

    items = []
    for description, hsn, low, high in rng.sample(gst_data.LINE_ITEM_CATALOG, rng.randint(1, 4)):
        quantity = rng.randint(10, 300) if high < 100 else rng.randint(1, 10)
        rate = round(rng.uniform(low, high), 2)
        items.append(
            {
                "description": description,
                "hsn_code": hsn,
                "quantity": quantity,
                "rate": rate,
                "amount": round(quantity * rate, 2),
            }
        )

    subtotal = round(sum(i["amount"] for i in items), 2)
    buyer = gst_data.BUYER
    if gstin[:2] == buyer["gstin"][:2]:
        cgst = round(subtotal * 0.09, 2)
        sgst = cgst
        igst = 0.0
    else:
        cgst = sgst = 0.0
        igst = round(subtotal * 0.18, 2)
    total = round(subtotal + cgst + sgst + igst, 2)

    if defect is Defect.WRONG_TOTAL:
        total = round(total + round(rng.uniform(100, 2500), 2), 2)

    invoice = {
        "invoice_number": number,
        "invoice_date": day.isoformat(),
        "vendor_name": vendor_name,
        "vendor_gstin": gstin,
        "buyer_name": buyer["name"],
        "buyer_gstin": buyer["gstin"],
        "place_of_supply": f"{buyer['state_name']} ({buyer['gstin'][:2]})",
        "line_items": items,
        "subtotal": subtotal,
        "cgst": cgst,
        "sgst": sgst,
        "igst": igst,
        "total": total,
    }
    render = {
        "city": city,
        "state_name": STATE_CODES.get(gstin[:2], ""),
        "street": f"Plot {rng.randint(1, 240)}, {rng.choice(['Industrial Estate', 'Business Park', 'Trade Centre', 'Main Road'])}",
        "pin": f"{rng.randint(100000, 899999)}",
        "printed_date": day.strftime(rng.choice(gst_data.DATE_STYLES)),
        "accent": rng.choice(gst_data.ACCENT_COLOURS),
    }
    return invoice, render, defect


def inr(value: float) -> str:
    sign = "-" if value < 0 else ""
    whole, fraction = f"{abs(value):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return f"{sign}{whole}.{fraction}"


def render_pdf(path: Path, invoice: dict, render: dict) -> None:
    accent = colors.HexColor(render["accent"])
    base = getSampleStyleSheet()["Normal"]
    normal = ParagraphStyle("normal", parent=base, fontName="Helvetica", fontSize=9, leading=12)
    bold = ParagraphStyle("bold", parent=normal, fontName="Helvetica-Bold")
    head = ParagraphStyle("head", parent=bold, textColor=colors.white)
    title = ParagraphStyle(
        "title", parent=normal, fontName="Helvetica-Bold", fontSize=16, leading=20, alignment=1, textColor=accent
    )

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Tax Invoice {invoice['invoice_number']}",
        author=invoice["vendor_name"],
    )

    vendor_block = Paragraph(
        f"<b>{escape(invoice['vendor_name'])}</b><br/>{escape(render['street'])}<br/>"
        f"{escape(render['city'])}, {escape(render['state_name'])} - {render['pin']}<br/>"
        f"GSTIN: {invoice['vendor_gstin']}",
        normal,
    )
    meta_block = Paragraph(
        f"Invoice No: <b>{escape(invoice['invoice_number'])}</b><br/>"
        f"Invoice Date: {render['printed_date']}<br/>"
        f"Place of Supply: {escape(invoice['place_of_supply'])}",
        normal,
    )
    header = Table([[vendor_block, meta_block]], colWidths=[100 * mm, 80 * mm])
    header.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    buyer = Table(
        [
            [
                Paragraph(
                    f"<b>Bill To</b><br/>{escape(invoice['buyer_name'])}<br/>"
                    f"GSTIN: {invoice['buyer_gstin']}",
                    normal,
                )
            ]
        ],
        colWidths=[180 * mm],
    )
    buyer.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    rows = [
        [
            Paragraph("Sr", head),
            Paragraph("Description", head),
            Paragraph("HSN/SAC", head),
            Paragraph("Qty", head),
            Paragraph("Rate (Rs.)", head),
            Paragraph("Amount (Rs.)", head),
        ]
    ]
    for index, item in enumerate(invoice["line_items"], start=1):
        rows.append(
            [
                str(index),
                Paragraph(escape(item["description"]), normal),
                item["hsn_code"],
                f"{item['quantity']:g}",
                inr(item["rate"]),
                inr(item["amount"]),
            ]
        )
    items = Table(rows, colWidths=[10 * mm, 70 * mm, 22 * mm, 15 * mm, 28 * mm, 35 * mm], repeatRows=1)
    items.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), accent),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (3, 1), (5, -1), "RIGHT"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )

    totals_rows = [["Subtotal", inr(invoice["subtotal"])]]
    if invoice["igst"] > 0:
        totals_rows.append(["IGST @ 18%", inr(invoice["igst"])])
    else:
        totals_rows.append(["CGST @ 9%", inr(invoice["cgst"])])
        totals_rows.append(["SGST @ 9%", inr(invoice["sgst"])])
    totals_rows.append(["Total", inr(invoice["total"])])
    totals = Table(totals_rows, colWidths=[45 * mm, 35 * mm], hAlign="RIGHT")
    totals.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, -1), (-1, -1), colors.whitesmoke),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )

    story = [
        Paragraph("TAX INVOICE", title),
        Spacer(1, 6 * mm),
        header,
        Spacer(1, 3 * mm),
        buyer,
        Spacer(1, 5 * mm),
        items,
        Spacer(1, 4 * mm),
        totals,
        Spacer(1, 10 * mm),
        Paragraph(
            f"Subject to {escape(render['city'])} jurisdiction. This is a computer generated invoice and does not "
            "require a signature.",
            normal,
        ),
    ]
    doc.build(story)


def generate_batch(
    count: int,
    settings: Settings,
    rng: random.Random,
    ledger_rows: list[dict],
    out_dir: Optional[Path] = None,
    today: Optional[date] = None,
    force_defect: Optional[Defect] = None,
) -> list[dict]:
    target = out_dir or settings.path("incoming_dir")
    target.mkdir(parents=True, exist_ok=True)
    current = today or date.today()
    used = {(r.get("gstin"), r.get("invoice_number")) for r in ledger_rows}
    records = []

    for _ in range(count):
        wanted = force_defect or pick_defect(rng, settings.pipeline.bad_invoice_ratio, bool(ledger_rows))
        invoice, render, actual = build_invoice(rng, wanted, ledger_rows, used, current)
        used.add((invoice["vendor_gstin"], invoice["invoice_number"]))
        slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{invoice['vendor_name']}_{invoice['invoice_number']}").strip("_")
        file_name = f"{slug}_{rng.randint(1000, 9999)}.pdf"
        render_pdf(target / file_name, invoice, render)
        records.append(
            {
                "file": file_name,
                "defect": actual.value,
                "expected_outcome": EXPECTED_OUTCOME[actual],
                "printed_date": render["printed_date"],
                "invoice": invoice,
            }
        )
    return records


def append_ground_truth(path: Path, records: list[dict]) -> None:
    existing: list[dict] = []
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing = loaded
        except json.JSONDecodeError:
            existing = []
    existing.extend(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate mock Indian GST invoices")
    parser.add_argument("--count", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--defect", choices=[d.value for d in Defect])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--no-truth", action="store_true")
    args = parser.parse_args(argv)

    settings = load_config()
    seed = args.seed if args.seed is not None else settings.pipeline.seed
    rng = random.Random(seed)
    ledger_rows = load_ledger_rows(settings.path("ledger_file"))
    count = args.count or settings.pipeline.invoices_per_run
    forced = Defect(args.defect) if args.defect else None

    records = generate_batch(count, settings, rng, ledger_rows, out_dir=args.out, force_defect=forced)
    if not args.no_truth:
        append_ground_truth(settings.path("ground_truth_file"), records)

    for record in records:
        print(f"{record['file']}  defect={record['defect']}  expected={record['expected_outcome']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())