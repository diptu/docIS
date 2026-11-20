"""ClaudeInvoiceLLM against a fake SDK client (no network)."""

from types import SimpleNamespace
from typing import Any

import pytest

from app.extraction import llm as llm_module
from app.extraction.llm import (
    FALLBACK_BETA,
    SYSTEM_PROMPT,
    ClaudeInvoiceLLM,
    ExtractionIncompleteError,
    ExtractionRefusedError,
)
from app.extraction.schema import InvoiceData
from tests.support.fakes import sample_invoice


class FakeStream:
    def __init__(self, message: Any) -> None:
        self.message = message

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get_final_message(self) -> Any:
        return self.message


class FakeAsyncAnthropic:
    """Records constructor and request arguments; returns a canned message."""

    instances: list["FakeAsyncAnthropic"] = []
    message: Any = None

    def __init__(self, **options: Any) -> None:
        self.options = options
        self.requests: list[dict[str, Any]] = []
        self.closed = False
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))
        FakeAsyncAnthropic.instances.append(self)

    def _stream(self, **request: Any) -> FakeStream:
        self.requests.append(request)
        return FakeStream(FakeAsyncAnthropic.message)

    async def __aenter__(self) -> "FakeAsyncAnthropic":
        return self

    async def __aexit__(self, *_: object) -> None:
        self.closed = True


def message(
    stop_reason: str = "end_turn", parsed: Any = "default", **extra: Any
) -> Any:
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=extra.get("stop_details"),
        parsed_output=sample_invoice() if parsed == "default" else parsed,
        model="claude-opus-5-5",
        usage=SimpleNamespace(
            input_tokens=1500, output_tokens=400, cache_read_input_tokens=900
        ),
    )


@pytest.fixture(autouse=True)
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAsyncAnthropic.instances = []
    FakeAsyncAnthropic.message = message()
    monkeypatch.setattr(llm_module.anthropic, "AsyncAnthropic", FakeAsyncAnthropic)


async def test_request_uses_structured_outputs_caching_and_fallbacks() -> None:
    llm = ClaudeInvoiceLLM(effort="high", max_tokens=1000, timeout_seconds=30)

    result = await llm.extract("Invoice INV-1\nTotal 10.00")

    [client] = FakeAsyncAnthropic.instances
    assert client.options == {"timeout": 30, "max_retries": 2}
    assert client.closed
    [request] = client.requests
    assert request["model"] == "claude-opus-5-5"
    assert request["max_tokens"] == 1000
    assert request["output_format"] is InvoiceData
    assert request["output_config"] == {"effort": "high"}
    assert request["fallbacks"] == "default"
    assert request["betas"] == [FALLBACK_BETA]
    assert request["system"] == [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    assert request["messages"] == [
        {
            "role": "user",
            "content": "<document>\nInvoice INV-1\nTotal 10.00\n</document>",
        }
    ]
    assert result.data == sample_invoice()
    assert (result.input_tokens, result.output_tokens, result.cache_read_tokens) == (
        1500,
        400,
        900,
    )


def test_system_prompt_has_no_volatile_content() -> None:
    """A stable prefix is what makes prompt caching work across documents."""
    assert "{" not in SYSTEM_PROMPT and "202" not in SYSTEM_PROMPT


async def test_refusal_is_reported() -> None:
    FakeAsyncAnthropic.message = message(
        "refusal", parsed=None, stop_details=SimpleNamespace(category="cyber")
    )

    with pytest.raises(ExtractionRefusedError, match="cyber"):
        await ClaudeInvoiceLLM().extract("text")


async def test_truncated_output_is_reported() -> None:
    FakeAsyncAnthropic.message = message("max_tokens", parsed=None)

    with pytest.raises(ExtractionIncompleteError, match="LLM_MAX_TOKENS"):
        await ClaudeInvoiceLLM().extract("text")


async def test_missing_structured_output_is_reported() -> None:
    FakeAsyncAnthropic.message = message(parsed=None)

    with pytest.raises(ExtractionIncompleteError, match="no structured output"):
        await ClaudeInvoiceLLM().extract("text")


async def test_missing_cache_usage_counts_as_zero() -> None:
    reply = message()
    reply.usage.cache_read_input_tokens = None
    FakeAsyncAnthropic.message = reply

    assert (await ClaudeInvoiceLLM().extract("text")).cache_read_tokens == 0


async def test_missing_credentials_become_a_permanent_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.extraction.llm import ExtractionConfigurationError
    from app.worker.retry import is_retryable

    def no_credentials(**_: Any) -> FakeStream:
        raise TypeError(
            '"Could not resolve authentication method. Expected one of api_key, '
            'auth_token, or credentials to be set."'
        )

    class NoKeyClient(FakeAsyncAnthropic):
        def _stream(self, **request: Any) -> FakeStream:
            return no_credentials(**request)

    monkeypatch.setattr(llm_module.anthropic, "AsyncAnthropic", NoKeyClient)

    with pytest.raises(ExtractionConfigurationError, match="ANTHROPIC_API_KEY") as info:
        await ClaudeInvoiceLLM().extract("text")
    assert is_retryable(info.value) is False


async def test_other_type_errors_are_not_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BuggyClient(FakeAsyncAnthropic):
        def _stream(self, **request: Any) -> FakeStream:
            raise TypeError("unexpected keyword argument 'foo'")

    monkeypatch.setattr(llm_module.anthropic, "AsyncAnthropic", BuggyClient)

    with pytest.raises(TypeError, match="unexpected keyword"):
        await ClaudeInvoiceLLM().extract("text")
