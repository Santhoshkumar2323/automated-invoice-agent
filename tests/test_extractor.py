import json
from types import SimpleNamespace

import pytest

from src.config import Secrets, Settings
from src.parsing import prompts
from src.parsing.extractor import make_extractor, parse_json_object
from src.validation.models import Invoice, LineItem


class FakeCompletions:
    def __init__(self, replies, error=None):
        self.replies = list(replies)
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        content = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=200),
        )


def fake_client(replies=("{}",), error=None):
    completions = FakeCompletions(replies, error)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


class HttpError(Exception):
    def __init__(self, status_code):
        super().__init__(f"http {status_code}")
        self.status_code = status_code


GOOD = json.dumps(prompts.EXAMPLE_INVOICE)


def test_prompt_example_is_a_valid_invoice():
    invoice = Invoice(**prompts.EXAMPLE_INVOICE)
    assert invoice.total == 18290.59


def test_prompt_lists_every_model_field():
    for name in list(Invoice.model_fields) + list(LineItem.model_fields):
        assert f'"{name}"' in prompts.SYSTEM_PROMPT


def test_prompt_tells_the_model_not_to_fix_values():
    assert "Never correct" in prompts.SYSTEM_PROMPT
    assert "day first" in prompts.SYSTEM_PROMPT
    assert "JSON" in prompts.SYSTEM_PROMPT


def test_extract_messages_put_the_invoice_last():
    messages = prompts.build_extract_messages("INVOICE TEXT HERE")
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[1]["content"].endswith("INVOICE TEXT HERE")


def test_repair_messages_carry_feedback_and_previous_output():
    messages = prompts.build_repair_messages("INVOICE TEXT HERE", {"total": 5}, "- ARITHMETIC_TOTAL: bad")
    user = messages[1]["content"]
    assert "ARITHMETIC_TOTAL: bad" in user
    assert '"total": 5' in user
    assert user.endswith("INVOICE TEXT HERE")
    assert "unchanged" in user


def test_system_prompt_is_identical_for_extract_and_repair():
    first = prompts.build_extract_messages("a")[0]
    second = prompts.build_repair_messages("a", {}, "x")[0]
    assert first == second

def test_extract_returns_parsed_data_tokens_and_model():
    client, completions = fake_client([GOOD])
    result = make_extractor(Settings(), Secrets(), client=client)("# md", None, None)
    assert result.data == prompts.EXAMPLE_INVOICE
    assert result.prompt_tokens == 1000
    assert result.completion_tokens == 200
    assert result.model == Settings().llm.model

def test_request_parameters_come_from_config():
    client, completions = fake_client([GOOD])
    settings = Settings(llm={"model": "some-model", "temperature": 0.2, "max_tokens": 900, "reasoning_effort": "medium"})
    make_extractor(settings, Secrets(), client=client)("# md", None, None)
    call = completions.calls[0]
    assert call["model"] == "some-model"
    assert call["temperature"] == 0.2
    assert call["max_completion_tokens"] == 900
    assert "max_tokens" not in call
    assert call["reasoning_effort"] == "medium"
    assert call["response_format"] == {"type": "json_object"}
    assert call["messages"][0]["role"] == "system"


def test_reasoning_effort_defaults_to_low():
    client, completions = fake_client([GOOD])
    make_extractor(Settings(), Secrets(), client=client)("# md", None, None)
    assert completions.calls[0]["reasoning_effort"] == "low"


def test_reasoning_effort_can_be_turned_off_for_non_reasoning_models():
    client, completions = fake_client([GOOD])
    settings = Settings(llm={"reasoning_effort": None})
    make_extractor(settings, Secrets(), client=client)("# md", None, None)
    assert "reasoning_effort" not in completions.calls[0]

def test_first_attempt_uses_extract_prompt_and_retry_uses_repair_prompt():
    client, completions = fake_client([GOOD])
    extract = make_extractor(Settings(), Secrets(), client=client)
    extract("INVOICE TEXT", None, None)
    extract("INVOICE TEXT", "- ARITHMETIC_TOTAL: bad", {"total": 1})
    first = completions.calls[0]["messages"][1]["content"]
    second = completions.calls[1]["messages"][1]["content"]
    assert "problems were found" not in first
    assert "problems were found" in second
    assert "ARITHMETIC_TOTAL" in second


def test_feedback_without_previous_falls_back_to_plain_extraction():
    client, completions = fake_client([GOOD])
    make_extractor(Settings(), Secrets(), client=client)("INVOICE TEXT", "- X: y", None)
    assert "problems were found" not in completions.calls[0]["messages"][1]["content"]


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        'Here is the result: {"a": 1} hope that helps',
        '  \n{"a": 1}\n  ',
    ],
)
def test_json_is_recovered_from_common_reply_shapes(text):
    assert parse_json_object(text) == {"a": 1}


@pytest.mark.parametrize("text", ["", "no json here", "{broken", "[1, 2]", '"just a string"', "{'a': 1}"])
def test_unusable_replies_become_an_empty_object(text):
    assert parse_json_object(text) == {}


def test_garbage_reply_still_reports_tokens():
    client, _ = fake_client(["I cannot do that"])
    result = make_extractor(Settings(), Secrets(), client=client)("# md", None, None)
    assert result.data == {}
    assert result.prompt_tokens == 1000
    assert result.completion_tokens == 200


def test_none_content_is_handled():
    client, completions = fake_client([None])
    result = make_extractor(Settings(), Secrets(), client=client)("# md", None, None)
    assert result.data == {}


@pytest.mark.parametrize(
    "status,fragment",
    [(401, "API key"), (403, "API key"), (429, "rate limit"), (413, "too large")],
)
def test_api_errors_become_readable_messages(status, fragment):
    client, _ = fake_client(error=HttpError(status))
    with pytest.raises(RuntimeError) as exc:
        make_extractor(Settings(), Secrets(), client=client)("# md", None, None)
    assert fragment in str(exc.value)


def test_unknown_api_error_keeps_its_type_name():
    client, _ = fake_client(error=TimeoutError("slow"))
    with pytest.raises(RuntimeError) as exc:
        make_extractor(Settings(), Secrets(), client=client)("# md", None, None)
    assert "TimeoutError" in str(exc.value)