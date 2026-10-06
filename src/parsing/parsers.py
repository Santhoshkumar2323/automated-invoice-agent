from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from langsmith import traceable

from src.config import Secrets, Settings
from src.workflow.state import ParseResult


def _friendly_error(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return "LlamaParse rejected the API key (check LLAMA_CLOUD_API_KEY)"
    if status == 402:
        return "LlamaParse credits are used up for this plan"
    if status == 429:
        return "LlamaParse rate limit reached"
    return f"{type(exc).__name__}: {exc}"


def extract_markdown(result) -> tuple[str, int]:
    markdown = getattr(result, "markdown", None)
    pages = list(getattr(markdown, "pages", None) or [])
    if not pages:
        raise ValueError("LlamaParse returned no Markdown pages")
    texts: list[str] = []
    failures: list[str] = []
    for page in pages:
        if getattr(page, "success", False):
            texts.append(page.markdown)
        else:
            failures.append(f"page {getattr(page, 'page_number', '?')}: {getattr(page, 'error', 'unknown error')}")
    if not texts:
        raise ValueError("LlamaParse could not read any page: " + "; ".join(failures))
    return "\n\n".join(texts), len(pages)


def make_parser(settings: Settings, secrets: Secrets, client=None) -> Callable[[Path], ParseResult]:
    if client is None:
        from llama_cloud import LlamaCloud

        client = LlamaCloud(api_key=secrets.llama_cloud_api_key)

    tier = settings.parser.tier
    timeout = float(settings.parser.timeout_seconds)

    @traceable(run_type="tool", name="llamaparse")
    def run_parse(file_name: str, pdf_bytes: bytes, tier_name: str) -> dict:
        result = client.parsing.parse(
            tier=tier_name,
            version="latest",
            upload_file=(file_name, pdf_bytes, "application/pdf"),
            expand=["markdown"],
            timeout=timeout,
        )
        markdown, pages = extract_markdown(result)
        return {"markdown": markdown, "pages": pages}

    def parse(path: Path) -> ParseResult:
        data = Path(path).read_bytes()
        try:
            output = run_parse(Path(path).name, data, tier)
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError(_friendly_error(exc)) from exc
        return ParseResult(markdown=output["markdown"], pages=output["pages"])

    return parse