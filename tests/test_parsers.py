from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config import Secrets, Settings
from src.parsing.parsers import extract_markdown, make_parser


def page(number, text=None, error=None):
    if error is not None:
        return SimpleNamespace(success=False, page_number=number, error=error)
    return SimpleNamespace(success=True, page_number=number, markdown=text)


class FakeParsing:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


def fake_client(result=None, error=None):
    return SimpleNamespace(parsing=FakeParsing(result, error))


def result_with(*pages):
    return SimpleNamespace(markdown=SimpleNamespace(pages=list(pages)))


class HttpError(Exception):
    def __init__(self, status_code):
        super().__init__(f"http {status_code}")
        self.status_code = status_code


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "invoice.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    return path


def test_single_page_is_returned_as_markdown(pdf):
    client = fake_client(result_with(page(1, "# Invoice")))
    parse = make_parser(Settings(), Secrets(), client=client)
    result = parse(pdf)
    assert result.markdown == "# Invoice"
    assert result.pages == 1


def test_multiple_pages_are_joined_and_counted(pdf):
    client = fake_client(result_with(page(1, "first"), page(2, "second")))
    result = make_parser(Settings(), Secrets(), client=client)(pdf)
    assert result.markdown == "first\n\nsecond"
    assert result.pages == 2


def test_request_uses_configured_tier_and_uploads_the_file(pdf):
    client = fake_client(result_with(page(1, "x")))
    settings = Settings(parser={"tier": "fast", "timeout_seconds": 120})
    make_parser(settings, Secrets(), client=client)(pdf)
    call = client.parsing.calls[0]
    assert call["tier"] == "fast"
    assert call["version"] == "latest"
    assert call["expand"] == ["markdown"]
    assert call["timeout"] == 120.0
    assert call["upload_file"] == ("invoice.pdf", b"%PDF-1.4 fake", "application/pdf")


def test_failed_page_is_skipped_but_still_billed(pdf):
    client = fake_client(result_with(page(1, "good"), page(2, error="bad scan")))
    result = make_parser(Settings(), Secrets(), client=client)(pdf)
    assert result.markdown == "good"
    assert result.pages == 2


def test_all_pages_failed_raises(pdf):
    client = fake_client(result_with(page(1, error="bad scan")))
    with pytest.raises(ValueError) as exc:
        make_parser(Settings(), Secrets(), client=client)(pdf)
    assert "bad scan" in str(exc.value)


def test_no_pages_raises(pdf):
    with pytest.raises(ValueError):
        make_parser(Settings(), Secrets(), client=fake_client(result_with()))(pdf)
    with pytest.raises(ValueError):
        make_parser(Settings(), Secrets(), client=fake_client(SimpleNamespace(markdown=None)))(pdf)


@pytest.mark.parametrize(
    "status,fragment",
    [(402, "credits"), (401, "API key"), (403, "API key"), (429, "rate limit")],
)
def test_http_errors_become_readable_messages(pdf, status, fragment):
    client = fake_client(error=HttpError(status))
    with pytest.raises(RuntimeError) as exc:
        make_parser(Settings(), Secrets(), client=client)(pdf)
    assert fragment in str(exc.value)


def test_unknown_error_keeps_its_type_name(pdf):
    client = fake_client(error=ConnectionError("boom"))
    with pytest.raises(RuntimeError) as exc:
        make_parser(Settings(), Secrets(), client=client)(pdf)
    assert "ConnectionError" in str(exc.value)


def test_missing_file_raises_file_not_found(tmp_path):
    client = fake_client(result_with(page(1, "x")))
    with pytest.raises(FileNotFoundError):
        make_parser(Settings(), Secrets(), client=client)(tmp_path / "nope.pdf")


def test_extract_markdown_helper_directly():
    text, pages = extract_markdown(result_with(page(1, "a")))
    assert (text, pages) == ("a", 1)