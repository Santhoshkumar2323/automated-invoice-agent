import sys
import types
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from src.browser.browser import _dismiss_popups, make_ledger_entry, playwright_session, submit_entry
from src.config import Settings

URL = "http://localhost:8501"
GSTIN = "33AAACA7712P1ZL"
INVOICE = {"vendor_name": "Alpha Labs", "vendor_gstin": GSTIN, "invoice_number": "INV-1", "total": 100.0}


class Locator:
    def __init__(self, page, kind, key):
        self.page = page
        self.kind = kind
        self.key = key

    @property
    def first(self):
        return self

    def wait_for(self, state="visible", timeout=None):
        return None

    def fill(self, value):
        return None

    def press(self, key):
        return None

    def click(self):
        if self.kind == "text" and "show again" in self.key:
            if self.page.popup_click_fails:
                raise RuntimeError("click failed")
            self.page.popup_visible = False
            self.page.events.append("dismissed")
        elif self.kind == "button":
            self.page.messages = ["Ledger Entry Verified", "Alpha Labs | INV-1 | INR 100.00"]

    def count(self):
        if self.kind == "text" and "show again" in self.key:
            return 1 if self.page.popup_visible else 0
        return len([m for m in self.page.messages if self.key.lower() in m.lower()])


class PopupPage:
    def __init__(self, popup=True, popup_click_fails=False):
        self.popup_visible = popup
        self.popup_click_fails = popup_click_fails
        self.messages = []
        self.events = []

    def goto(self, url, timeout=None):
        return None

    def get_by_label(self, text, exact=None):
        return Locator(self, "field", text)

    def get_by_role(self, role, name=None):
        return Locator(self, "button", name)

    def get_by_text(self, text, exact=None):
        return Locator(self, "text", text)

    def wait_for_timeout(self, ms):
        return None

    def screenshot(self, path=None, full_page=False):
        self.events.append("popup_in_shot" if self.popup_visible else "clean_shot")
        Path(path).write_bytes(b"png")


def test_popup_is_dismissed_before_the_screenshot(tmp_path):
    page = PopupPage()
    result = submit_entry(page, INVOICE, URL, 1000, tmp_path / "a.png")
    assert result.success is True
    assert page.events == ["dismissed", "clean_shot"]


def test_popup_that_appears_late_is_still_dismissed_before_the_shot(tmp_path):
    page = PopupPage(popup=False)

    class LateLocator(Locator):
        def click(self):
            if self.kind == "button":
                page.popup_visible = True
            super().click()

    page.get_by_role = lambda role, name=None: LateLocator(page, "button", name)
    result = submit_entry(page, INVOICE, URL, 1000, tmp_path / "a.png")
    assert result.success is True
    assert page.events == ["dismissed", "clean_shot"]


def test_popup_handling_can_be_switched_off(tmp_path):
    page = PopupPage()
    submit_entry(page, INVOICE, URL, 1000, tmp_path / "a.png", dismiss_popups=False)
    assert page.events == ["popup_in_shot"]


def test_a_popup_that_cannot_be_closed_does_not_break_the_entry(tmp_path):
    page = PopupPage(popup_click_fails=True)
    result = submit_entry(page, INVOICE, URL, 1000, tmp_path / "a.png")
    assert result.success is True


def test_no_popup_means_nothing_is_clicked():
    page = PopupPage(popup=False)
    _dismiss_popups(page)
    assert page.events == []


def test_screenshot_name_starts_with_a_sortable_timestamp(tmp_path):
    page = PopupPage(popup=False)

    @contextmanager
    def factory(settings):
        yield page

    settings = Settings(paths={"screenshots_dir": str(tmp_path / "shots")}, browser={"timeout_ms": 1000})
    clock = lambda: datetime(2026, 10, 3, 8, 41, 37, tzinfo=timezone.utc)
    result = make_ledger_entry(settings, session_factory=factory, clock=clock)(INVOICE, "My File (1).pdf")
    assert Path(result.screenshot).name == "20261003_084137_My_File_1.png"


class FakeBrowser:
    def __init__(self, log):
        self.log = log

    def new_page(self, viewport=None):
        self.log["viewport"] = viewport
        return "page"

    def close(self):
        self.log["closed"] = True


class FakePlaywright:
    def __init__(self, log):
        self.chromium = types.SimpleNamespace(launch=self.launch)
        self.log = log

    def launch(self, headless=None, slow_mo=None):
        self.log["headless"] = headless
        self.log["slow_mo"] = slow_mo
        return FakeBrowser(self.log)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def install_fake_playwright(monkeypatch, log):
    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = lambda: FakePlaywright(log)
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)


def test_browser_window_size_and_mode_come_from_the_config(monkeypatch):
    log = {}
    install_fake_playwright(monkeypatch, log)
    settings = Settings(browser={"headless": False, "slow_mo_ms": 250, "viewport_width": 1600, "viewport_height": 2000})
    with playwright_session(settings) as page:
        assert page == "page"
    assert log["viewport"] == {"width": 1600, "height": 2000}
    assert log["headless"] is False
    assert log["slow_mo"] == 250
    assert log["closed"] is True


def test_default_window_is_tall_enough_for_the_whole_page(monkeypatch):
    log = {}
    install_fake_playwright(monkeypatch, log)
    with playwright_session(Settings()):
        pass
    assert log["viewport"] == {"width": 1280, "height": 1400}