from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from src.config import ROOT, Settings
from src.ledger import SUCCESS_TOKEN
from src.scheduler import utc_now
from src.workflow.state import EntryResult

REJECTED_TOKEN = "Entry rejected"
SUBMIT_BUTTON = "Submit Ledger Entry"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_") or "entry"


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def check_ledger_site(url: str, timeout: float = 5.0) -> Optional[str]:
    target = url.rstrip("/") + "/_stcore/health"
    try:
        with urllib.request.urlopen(target, timeout=timeout) as response:
            status = getattr(response, "status", 200)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return f"ledger website is not reachable at {url} ({exc}). Start it with: streamlit run app/ledger_app.py"
    if status != 200:
        return f"ledger website at {url} answered with HTTP {status}"
    return None


def friendly_error(exc: Exception, url: str) -> str:
    text = str(exc)
    if "Executable doesn't exist" in text or "playwright install" in text:
        return "Chromium is not installed for Playwright. Run: playwright install chromium"
    if "ERR_CONNECTION_REFUSED" in text or "ERR_CONNECTION_RESET" in text:
        return f"cannot reach the ledger website at {url}. Start it with: streamlit run app/ledger_app.py"
    if "Timeout" in type(exc).__name__ or "Timeout" in text:
        return f"timed out waiting for the ledger website at {url}"
    lines = text.strip().splitlines()
    return f"{type(exc).__name__}: {lines[0] if lines else ''}".strip()


def _dismiss_popups(page) -> None:
    try:
        button = page.get_by_text("show again")
        if button.count() > 0:
            button.first.click()
            page.wait_for_timeout(300)
    except Exception:
        return


def _take_screenshot(page, path: Path, dismiss: bool = True) -> str:
    if dismiss:
        _dismiss_popups(page)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=True)
    except Exception:
        return ""
    return _display_path(path)


def _wait_for_outcome(page, timeout_ms: int) -> str:
    success = page.get_by_text(SUCCESS_TOKEN)
    rejected = page.get_by_text(REJECTED_TOKEN)
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        if success.count() > 0:
            return "success"
        if rejected.count() > 0:
            return "rejected"
        if time.monotonic() >= deadline:
            return "timeout"
        page.wait_for_timeout(200)


def _wait_for_text(page, text: str, timeout_ms: int) -> bool:
    locator = page.get_by_text(text)
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        if locator.count() > 0:
            return True
        if time.monotonic() >= deadline:
            return False
        page.wait_for_timeout(200)


def submit_entry(
    page,
    invoice: dict,
    url: str,
    timeout_ms: int,
    screenshot_path: Path,
    dismiss_popups: bool = True,
) -> EntryResult:
    failed_path = screenshot_path.with_name(screenshot_path.stem + "_failed.png")
    try:
        page.goto(url, timeout=timeout_ms)
        page.get_by_label("Vendor Name", exact=True).wait_for(state="visible", timeout=timeout_ms)
        page.wait_for_timeout(300)
        if dismiss_popups:
            _dismiss_popups(page)

        fields = (
            ("Vendor Name", str(invoice["vendor_name"])),
            ("GSTIN", str(invoice["vendor_gstin"])),
            ("Invoice Number", str(invoice["invoice_number"])),
            ("Amount (INR)", f"{float(invoice['total']):.2f}"),
        )
        for label, value in fields:
            field = page.get_by_label(label, exact=True)
            field.fill(value)
            field.press("Tab")

        page.get_by_role("button", name=SUBMIT_BUTTON).click()
        outcome = _wait_for_outcome(page, timeout_ms)

        if outcome == "rejected":
            text = page.get_by_text(REJECTED_TOKEN).first.inner_text()
            reason = re.sub(r"^\s*" + REJECTED_TOKEN + r":?\s*", "", text).strip() or text
            return EntryResult(False, _take_screenshot(page, failed_path, dismiss_popups), reason)

        if outcome == "timeout":
            message = f"no confirmation or rejection message appeared within {timeout_ms} ms"
            return EntryResult(False, _take_screenshot(page, failed_path, dismiss_popups), message)

        caption = f"{invoice['invoice_number']} | INR"
        if not _wait_for_text(page, caption, min(timeout_ms, 3000)):
            message = "the confirmation did not show the submitted invoice number"
            return EntryResult(False, _take_screenshot(page, failed_path, dismiss_popups), message)

        return EntryResult(True, _take_screenshot(page, screenshot_path, dismiss_popups), "ledger entry verified")
    except Exception as exc:
        return EntryResult(False, _take_screenshot(page, failed_path, dismiss_popups), friendly_error(exc, url))


@contextmanager
def playwright_session(settings: Settings):
    from playwright.sync_api import sync_playwright

    config = settings.browser
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=config.headless, slow_mo=config.slow_mo_ms)
        try:
            yield browser.new_page(viewport={"width": config.viewport_width, "height": config.viewport_height})
        finally:
            browser.close()


def make_ledger_entry(
    settings: Settings,
    session_factory=playwright_session,
    clock: Callable[[], datetime] = utc_now,
) -> Callable[[dict, str], EntryResult]:
    config = settings.browser
    screenshots = settings.path("screenshots_dir")

    def enter(invoice: dict, file_name: str) -> EntryResult:
        stamp = clock().strftime("%Y%m%d_%H%M%S")
        target = screenshots / f"{stamp}_{_slug(Path(file_name).stem)}.png"
        try:
            with session_factory(settings) as page:
                return submit_entry(page, invoice, config.ledger_url, config.timeout_ms, target, config.dismiss_popups)
        except Exception as exc:
            return EntryResult(False, message=friendly_error(exc, config.ledger_url))

    return enter