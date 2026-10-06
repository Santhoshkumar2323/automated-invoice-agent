from __future__ import annotations

from typing import Optional

REGISTRY: dict[str, dict[str, str]] = {
    "27AABCQ4821M1Z7": {"name": "Quantum Tech Labs", "status": "ACTIVE", "city": "Mumbai"},
    "33AAACA7712P1ZL": {"name": "Alpha Labs", "status": "ACTIVE", "city": "Chennai"},
    "29AABCN3390H1ZB": {"name": "Nexus Industrial Supplies", "status": "ACTIVE", "city": "Bengaluru"},
    "33AAECS6104R1Z3": {"name": "Sundaram Office Systems", "status": "ACTIVE", "city": "Coimbatore"},
    "36AABCV9921D1ZB": {"name": "Veda Pharma Distributors", "status": "ACTIVE", "city": "Hyderabad"},
    "07AAACO5518B1ZV": {"name": "Orbit Cloud Services", "status": "ACTIVE", "city": "New Delhi"},
    "24AADCK2207E1Z6": {"name": "Kaveri Packaging Works", "status": "ACTIVE", "city": "Ahmedabad"},
    "27AABCZ8830L1ZR": {"name": "Zenith Auto Components", "status": "ACTIVE", "city": "Pune"},
    "33AABCA1234F1ZG": {"name": "Alpha Logistics Sandbox", "status": "SUSPENDED", "city": "Chennai"},
    "27AAACR4410G1ZR": {"name": "Redwood Traders", "status": "SUSPENDED", "city": "Mumbai"},
    "24AABCP7765J1Z3": {"name": "Pinnacle Metals", "status": "REVOKED", "city": "Surat"},
}


def lookup(gstin: str) -> Optional[dict[str, str]]:
    return REGISTRY.get((gstin or "").strip().upper())


def get_status(gstin: str) -> str:
    record = lookup(gstin)
    return record["status"] if record else "NOT_FOUND"


def vendors_with_status(status: str) -> list[str]:
    return [g for g, r in REGISTRY.items() if r["status"] == status]