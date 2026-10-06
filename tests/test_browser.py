import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError

import pytest

from src.browser import browser as browser_module
from src.browser.browser import (
    _display_path,
    _slug,
    check_ledger_site,
    friendly_error,
    make_ledger_entry,
    submit_entry,
)
from src.config import ROOT, Settings
from src.ledger import LedgerError, add_entry, read_rows

URL = "http://localhost:8501"
GSTIN = "33AAACA7712P1ZL"


class FakeLocator:
    def __init__(self, page, kind, key):
        self.page = page
        self.kind = kind
        self.key = key

    @property
    def first(self):
        return self

    def wait_for(self, state="visible", timeout=None):
        if self.page.never_loads:
            raise TimeoutError("Timeout 1000ms exceeded.")

    def fill(self, value):
        self.page.fields[self.key] = value

    def press(self, key):
        self.page.pressed.append((self.key, key))

    def click(self):
        self.page.click_button(self.key)

    def count(self):
        return len(self.page.matching(self.key))

    def inner_text(self):
        return self.page.matching(self.key)[0]


class FakePage:
    def __init__(self, ledger_path, mode="normal", down=False, never_loads=False):
        self.ledger_path = ledger_path
        self.mode = mode
        self.down = down
        self.never_loads = never_loads
        self.visited = []
        self.fields = {}
        self.pressed = []
        self.messages = []
        self.screenshots = []

    def goto(self, url, timeout=None):
        if self.down:
            raise Exception(f"net::ERR_CONNECTION_REFUSED at {url}")
        self.visited.append(url)

    def get_by_label(self, text, exact=None):
        return FakeLocator(self, "field", text)

    def get_by_role(self, role, name=None):
        return FakeLocator(self, "button", name)

    def get_by_text(self, text, exact=None):
        return FakeLocator(self, "text", text)

    def wait_for_timeout(self, ms):
        time.sleep(0.005)

    def screenshot(self, path=None, full_page=False):
        if self.mode == "screenshot_fails":
            raise RuntimeError("cannot capture")
        Path(path).write_bytes(b"png")
        self.screenshots.append(Path(path).name)

    def matching(self, text):
        return [m for m in self.messages if text.lower() in m.lower()]

    def click_button(self, name):
        assert name == "Submit Ledger Entry"
        if self.mode == "silent":
            return
        try:
            row = add_entry(
                self.ledger_path,
                self.fields.get("Vendor Name", ""),
                self.fields.get("GSTIN", ""),
                self.fields.get("Invoice Number", ""),
                self.fields.get("Amount (INR)", ""),
            )
        except LedgerError as exc:
            self.messages = [f"Entry rejected: {exc}"]
            return
        number = "WRONG-999" if self.mode == "wrong_caption" else row["invoice_number"]
        self.messages = ["Ledger Entry Verified", f"{row['vendor']} | {number} | INR {row['amount']:,.2f}"]


def invoice(number="INV-1", total=59000.0, gstin=GSTIN, vendor="Alpha Labs"):
    return {"vendor_name": vendor, "vendor_gstin": gstin, "invoice_number": number, "total": total}


def run(tmp_path, page, inv=None, timeout_ms=1000, name="a.png"):
    return submit_entry(page, inv or invoice(), URL, timeout_ms, tmp_path / "shots" / name)


def test_successful_entry_is_typed_submitted_and_saved(tmp_path):
    ledger = tmp_path / "ledger.json"
    page = FakePage(ledger)
    result = run(tmp_path, page)
    assert result.success is True
    assert result.message == "ledger entry verified"
    assert Path(result.screenshot).exists()
    assert Path(result.screenshot).name == "a.png"
    assert page.visited == [URL]
    assert page.fields == {
        "Vendor Name": "Alpha Labs",
        "GSTIN": GSTIN,
        "Invoice Number": "INV-1",
        "Amount (INR)": "59000.00",
    }
    assert [key for _, key in page.pressed] == ["Tab"] * 4
    rows = read_rows(ledger)
    assert len(rows) == 1 and rows[0]["invoice_number"] == "INV-1"


@pytest.mark.parametrize("total,text", [(209600.2, "209600.20"), (1500, "1500.00"), ("2500.5", "2500.50")])
def test_amount_is_typed_with_two_decimals(tmp_path, total, text):
    page = FakePage(tmp_path / "ledger.json")
    run(tmp_path, page, invoice(total=total))
    assert page.fields["Amount (INR)"] == text


def test_duplicate_is_reported_with_the_websites_reason(tmp_path):
    ledger = tmp_path / "ledger.json"
    add_entry(ledger, "Alpha Labs", GSTIN, "INV-1", "100")
    result = run(tmp_path, FakePage(ledger))
    assert result.success is False
    assert "already exists" in result.message
    assert not result.message.startswith("Entry rejected")
    assert result.screenshot.endswith("a_failed.png")
    assert Path(result.screenshot).exists()
    assert len(read_rows(ledger)) == 1


def test_bad_gstin_is_reported(tmp_path):
    result = run(tmp_path, FakePage(tmp_path / "ledger.json"), invoice(gstin="BAD"))
    assert result.success is False
    assert "GSTIN" in result.message


def test_website_down_gives_a_clear_message(tmp_path):
    ledger = tmp_path / "ledger.json"
    result = run(tmp_path, FakePage(ledger, down=True))
    assert result.success is False
    assert "cannot reach the ledger website" in result.message
    assert "streamlit run app/ledger_app.py" in result.message
    assert not ledger.exists()


def test_page_that_never_loads_times_out_clearly(tmp_path):
    result = run(tmp_path, FakePage(tmp_path / "ledger.json", never_loads=True))
    assert result.success is False
    assert "timed out" in result.message


def test_no_message_at_all_is_a_timeout(tmp_path):
    result = run(tmp_path, FakePage(tmp_path / "ledger.json", mode="silent"), timeout_ms=300)
    assert result.success is False
    assert "no confirmation or rejection message appeared within 300 ms" in result.message
    assert result.screenshot.endswith("a_failed.png")


def test_confirmation_for_a_different_invoice_is_not_accepted(tmp_path):
    result = run(tmp_path, FakePage(tmp_path / "ledger.json", mode="wrong_caption"), timeout_ms=300)
    assert result.success is False
    assert "did not show the submitted invoice number" in result.message


def test_failed_screenshot_does_not_turn_success_into_failure(tmp_path):
    result = run(tmp_path, FakePage(tmp_path / "ledger.json", mode="screenshot_fails"))
    assert result.success is True
    assert result.screenshot == ""


def test_missing_invoice_field_is_reported_not_raised(tmp_path):
    page = FakePage(tmp_path / "ledger.json")
    result = run(tmp_path, page, {"vendor_name": "Alpha Labs"})
    assert result.success is False
    assert "KeyError" in result.message


def session_for(page):
    @contextmanager
    def factory(settings):
        yield page

    return factory


def settings_for(tmp_path):
    return Settings(
        paths={"screenshots_dir": str(tmp_path / "shots")},
        browser={"ledger_url": URL, "timeout_ms": 1000},
    )


def test_make_ledger_entry_names_the_screenshot_after_the_file(tmp_path):
    page = FakePage(tmp_path / "ledger.json")
    enter = make_ledger_entry(settings_for(tmp_path), session_factory=session_for(page))
    result = enter(invoice(), "My File (1).pdf")
    assert result.success is True
    name = Path(result.screenshot).name
    assert name.endswith("_My_File_1.png")
    assert name[:8].isdigit() and name[8] == "_"

def test_browser_not_installed_gives_the_fix(tmp_path):
    @contextmanager
    def broken(settings):
        raise Exception("BrowserType.launch: Executable doesn't exist at /x. Please run playwright install")
        yield

    enter = make_ledger_entry(settings_for(tmp_path), session_factory=broken)
    result = enter(invoice(), "a.pdf")
    assert result.success is False
    assert "playwright install chromium" in result.message


@pytest.mark.parametrize(
    "error,fragment",
    [
        (Exception("net::ERR_CONNECTION_REFUSED at http://x"), "cannot reach the ledger website"),
        (Exception("net::ERR_CONNECTION_RESET"), "cannot reach the ledger website"),
        (TimeoutError("Timeout 5000ms exceeded"), "timed out"),
        (Exception("Executable doesn't exist"), "playwright install chromium"),
        (ValueError("something odd\nsecond line"), "ValueError: something odd"),
    ],
)
def test_friendly_error_messages(error, fragment):
    assert fragment in friendly_error(error, URL)


class FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_site_check_passes_when_health_endpoint_answers(monkeypatch):
    asked = []

    def fake_urlopen(target, timeout=None):
        asked.append(target)
        return FakeResponse(200)

    monkeypatch.setattr(browser_module.urllib.request, "urlopen", fake_urlopen)
    assert check_ledger_site("http://localhost:8501/") is None
    assert asked == ["http://localhost:8501/_stcore/health"]


def test_site_check_reports_an_unreachable_website(monkeypatch):
    def fake_urlopen(target, timeout=None):
        raise URLError("connection refused")

    monkeypatch.setattr(browser_module.urllib.request, "urlopen", fake_urlopen)
    message = check_ledger_site(URL)
    assert "not reachable" in message
    assert "streamlit run app/ledger_app.py" in message


def test_site_check_reports_an_unhealthy_website(monkeypatch):
    monkeypatch.setattr(browser_module.urllib.request, "urlopen", lambda target, timeout=None: FakeResponse(500))
    assert "HTTP 500" in check_ledger_site(URL)


def test_slug_and_display_path_helpers(tmp_path):
    assert _slug("My File (1)") == "My_File_1"
    assert _slug("###") == "entry"
    assert _display_path(ROOT / "screenshots" / "x.png") == "screenshots/x.png"
    assert _display_path(tmp_path / "x.png") == str(tmp_path / "x.png")