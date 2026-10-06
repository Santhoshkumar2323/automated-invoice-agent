from __future__ import annotations

import json
import re
from typing import Optional

from langsmith import traceable

from src.config import Secrets, Settings
from src.parsing import prompts
from src.workflow.state import ExtractResult


def _friendly_error(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return "Groq rejected the API key (check GROQ_API_KEY)"
    if status == 429:
        return "Groq rate limit reached (free tier limit)"
    if status == 413:
        return "Groq request too large"
    return f"{type(exc).__name__}: {exc}"


def parse_json_object(text: str) -> dict:
    cleaned = (text or "").strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end <= start:
            return {}
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def make_extractor(settings: Settings, secrets: Secrets, client=None):
    if client is None:
        from groq import Groq

        client = Groq(api_key=secrets.groq_api_key, max_retries=5, timeout=90.0)

    model = settings.llm.model

    @traceable(run_type="llm", name="groq_extract", metadata={"ls_provider": "groq", "ls_model_name": model})
    def call_model(messages: list[dict]) -> dict:
        request = {
            "model": model,
            "messages": messages,
            "temperature": settings.llm.temperature,
            "max_completion_tokens": settings.llm.max_tokens,
            "response_format": {"type": "json_object"},
        }
        if settings.llm.reasoning_effort:
            request["reasoning_effort"] = settings.llm.reasoning_effort
        response = client.chat.completions.create(**request)
        usage = response.usage
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return {
            "content": response.choices[0].message.content or "",
            "usage_metadata": {
                "input_tokens": prompt_tokens,
                "output_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    def extract(markdown: str, feedback: Optional[str], previous: Optional[dict]) -> ExtractResult:
        if feedback and previous is not None:
            messages = prompts.build_repair_messages(markdown, previous, feedback)
        else:
            messages = prompts.build_extract_messages(markdown)
        try:
            reply = call_model(messages)
        except Exception as exc:
            raise RuntimeError(_friendly_error(exc)) from exc
        usage = reply["usage_metadata"]
        return ExtractResult(
            data=parse_json_object(reply["content"]),
            prompt_tokens=usage["input_tokens"],
            completion_tokens=usage["output_tokens"],
            model=model,
        )

    return extract