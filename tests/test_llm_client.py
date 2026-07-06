"""LLM client: response-shape edge cases, using a mocked OpenAI client so
these run offline with no network or 9router dependency.

Covers two real failures hit against a live router: a response with
``choices=None`` entirely (not just empty - crashes a bare index with a
confusing TypeError instead of a clear error), and a response where the model
tried to call tools instead of replying with text (content is None,
finish_reason is "tool_calls") - both should raise LLMClientError with a
useful message, never an unhandled crash.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orchestrator.config import Settings
from orchestrator.llm_client import LLMClient, LLMClientError


@pytest.fixture()
def llm():
    client = LLMClient(Settings())
    client._client = MagicMock()  # replace the real OpenAI client with a mock
    return client


def _fake_response(choices, data=None):
    resp = MagicMock()
    resp.choices = choices
    resp.data = data
    resp.model_dump_json.return_value = '{"choices": null}'
    return resp


def test_normal_reply_is_returned(llm):
    message = SimpleNamespace(content="1. do the thing", finish_reason="stop")
    llm._client.chat.completions.create.return_value = _fake_response(
        [SimpleNamespace(message=message, finish_reason="stop")]
    )
    assert llm.chat("head", system="s", user="u") == "1. do the thing"


def test_choices_is_none_raises_clear_error_not_a_crash(llm):
    llm._client.chat.completions.create.return_value = _fake_response(None)
    with pytest.raises(LLMClientError, match="no choices at all"):
        llm.chat("head", system="s", user="u")


def test_choices_is_empty_list_raises_clear_error(llm):
    llm._client.chat.completions.create.return_value = _fake_response([])
    with pytest.raises(LLMClientError, match="no choices at all"):
        llm.chat("head", system="s", user="u")


def test_model_calls_tools_instead_of_replying(llm):
    # content is None, the model chose tool_calls instead - a real backend
    # behavior seen against a combo route that turned out to be an agent
    # runtime rather than a plain chat model.
    message = SimpleNamespace(content=None, finish_reason="tool_calls")
    llm._client.chat.completions.create.return_value = _fake_response(
        [SimpleNamespace(message=message, finish_reason="tool_calls")]
    )
    with pytest.raises(LLMClientError, match="empty reply"):
        llm.chat("head", system="s", user="u")


def test_reply_wrapped_in_a_data_envelope_is_unwrapped(llm):
    # A real response seen from a live 9router combo: the top-level response
    # looks like an empty shell (choices: null, model: null, ...) while the
    # actual generated text sits one level deeper under response.data.choices
    # - a plain dict, since it's not a field the openai SDK's schema knows
    # about. Must be found and used rather than treated as "no reply".
    data = {"choices": [{"finish_reason": "stop", "message": {"content": "1. Add the endpoint."}}]}
    llm._client.chat.completions.create.return_value = _fake_response(None, data=data)
    assert llm.chat("head", system="s", user="u") == "1. Add the endpoint."


def test_request_sends_tool_choice_none(llm):
    message = SimpleNamespace(content="ok", finish_reason="stop")
    llm._client.chat.completions.create.return_value = _fake_response(
        [SimpleNamespace(message=message, finish_reason="stop")]
    )
    llm.chat("head", system="s", user="u")
    _, kwargs = llm._client.chat.completions.create.call_args
    assert kwargs["tool_choice"] == "none"


def test_max_tokens_differs_for_plan_vs_code_roles(llm):
    message = SimpleNamespace(content="ok", finish_reason="stop")
    llm._client.chat.completions.create.return_value = _fake_response(
        [SimpleNamespace(message=message, finish_reason="stop")]
    )
    llm.chat("head", system="s", user="u")
    plan_tokens = llm._client.chat.completions.create.call_args.kwargs["max_tokens"]
    llm.chat("developer", system="s", user="u")
    code_tokens = llm._client.chat.completions.create.call_args.kwargs["max_tokens"]

    assert plan_tokens == Settings().llm_max_tokens_plan
    assert code_tokens == Settings().llm_max_tokens_code
    assert code_tokens > plan_tokens  # whole-file replies need far more room


def test_transient_failure_is_retried_then_succeeds(llm, monkeypatch):
    monkeypatch.setattr("orchestrator.llm_client.time.sleep", lambda _: None)  # no real backoff in tests
    good = _fake_response([SimpleNamespace(message=SimpleNamespace(content="recovered", finish_reason="stop"), finish_reason="stop")])
    # First attempt: empty shell (no choices anywhere). Second attempt: fine.
    llm._client.chat.completions.create.side_effect = [_fake_response(None), good]

    assert llm.chat("head", system="s", user="u") == "recovered"
    assert llm._client.chat.completions.create.call_count == 2


def test_persistent_failure_exhausts_retries_with_attempt_count(llm, monkeypatch):
    monkeypatch.setattr("orchestrator.llm_client.time.sleep", lambda _: None)
    llm._client.chat.completions.create.return_value = _fake_response(None)

    with pytest.raises(LLMClientError, match="after 3 attempts"):
        llm.chat("head", system="s", user="u")  # 1 try + 2 retries (default)
    assert llm._client.chat.completions.create.call_count == 3
