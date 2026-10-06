from __future__ import annotations

import random

BUYER = {
    "name": "Meridian Retail Pvt Ltd",
    "gstin": "33AABCM5612K1ZN",
    "city": "Chennai",
    "state_name": "Tamil Nadu",
}

STATE_CITIES = {
    "07": "New Delhi",
    "24": "Ahmedabad",
    "27": "Mumbai",
    "29": "Bengaluru",
    "32": "Kochi",
    "33": "Chennai",
    "36": "Hyderabad",
}

UNKNOWN_VENDOR_NAMES = [
    "Horizon Stationers",
    "Lakshmi Trading Co",
    "Bluewave Electricals",
    "Ganga Textiles Pvt Ltd",
    "Apex Hardware Mart",
    "Sree Ram Enterprises",
]

LINE_ITEM_CATALOG = [
    ("Laptop computer 14 inch", "8471", 38000.0, 72000.0),
    ("Office chair ergonomic", "9401", 3500.0, 9500.0),
    ("Cloud hosting subscription monthly", "9983", 5000.0, 45000.0),
    ("Corrugated packaging boxes", "4819", 12.0, 60.0),
    ("Freight and logistics service", "9965", 2500.0, 30000.0),
    ("LED display monitor 24 inch", "8528", 7500.0, 18000.0),
    ("Printer toner cartridge", "8443", 1800.0, 6500.0),
    ("Software licence annual", "9973", 10000.0, 90000.0),
    ("Industrial lubricant 20L drum", "2710", 4200.0, 11000.0),
    ("Network switch 24 port", "8517", 6500.0, 28000.0),
    ("Maintenance and support service", "9987", 3000.0, 25000.0),
    ("Stainless steel fasteners kit", "7318", 450.0, 2200.0),
]

DATE_STYLES = ["%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%Y-%m-%d"]

ACCENT_COLOURS = ["#1f3a5f", "#7a1f2b", "#1f5f4a", "#4a2f7a", "#5f4a1f"]


def random_pan(rng: random.Random) -> str:
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    return (
        "".join(rng.choice(letters) for _ in range(3))
        + "C"
        + rng.choice(letters)
        + f"{rng.randint(0, 9999):04d}"
        + rng.choice(letters)
    )


def financial_year(day) -> str:
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start}-{str(start + 1)[-2:]}"