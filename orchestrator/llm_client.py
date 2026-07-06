"""A thin wrapper around 9router — a local proxy that speaks the standard
OpenAI chat-completions API and fans requests out to whichever of your
connected providers you've configured behind it.

Using the official ``openai`` package pointed at a custom ``base_url`` is the
normal way to talk to any OpenAI-compatible gateway (LiteLLM, OpenRouter-style
proxies, and 9router alike) — no bespoke HTTP code needed. If 9router's real
path turns out not to be ``/v1``, this is the one line to change
(``NINEROUTER_BASE_URL`` in ``.env``).
"""

from __future__ import annotations

import time

import httpx
from openai import APIError, OpenAI

from .config import Settings

# Role name -> the Settings field holding that role's model string.
_ROLE_MODEL_FIELD = {
    "head": "model_head",
    "developer": "model_developer",
    "realtime_fixer": "model_realtime_fixer",
    "makefile_tester": "model_makefile_tester",
}

# Roles that produce whole-file code output get the larger token budget;
# everything else replies in short prose (plans, verdicts).
_CODE_ROLES = {"developer", "realtime_fixer"}


class LLMClientError(RuntimeError):
    """Raised when 9router can't be reached or rejects a request outright."""


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # trust_env=False bypasses any machine-level HTTP proxy that would
        # otherwise intercept localhost — the openai SDK's default httpx
        # client honours HTTP_PROXY/system proxy settings unless told not to,
        # which is exactly what breaks 9router (also local, also on
        # localhost) on machines with a proxy agent watching the loopback
        # interface (see board_client.py's httpx.Client for the same fix).
        self._client = OpenAI(
            base_url=settings.ninerouter_base_url,
            api_key=settings.ninerouter_api_key,
            # Bounded per-request time: combo routes have been seen taking
            # 120s+ through internal proxy fallbacks; without a ceiling a hung
            # route stalls the whole run. The SDK's own retry is disabled in
            # favour of our loop below, which also covers empty-shell replies.
            http_client=httpx.Client(trust_env=False, timeout=settings.llm_timeout_seconds),
            max_retries=0,
        )

    def model_for(self, role: str) -> str:
        field = _ROLE_MODEL_FIELD.get(role)
        if field is None:
            raise LLMClientError(f"Unknown agent role {role!r}; add it to _ROLE_MODEL_FIELD")
        return getattr(self._settings, field)

    def chat(self, role: str, *, system: str, user: str) -> str:
        """One logical call for the given role, with bounded retries around
        transient failures (dropped connections, 5xx, flaky combo routes).
        Returns the reply text.
        """
        attempts = 1 + max(0, self._settings.llm_max_retries)
        last_error: LLMClientError | None = None
        for attempt in range(attempts):
            if attempt:
                # Small linear backoff - enough to let a combo's route churn
                # settle without turning a failed run into a long stall.
                time.sleep(2.0 * attempt)
            try:
                return self._chat_once(role, system=system, user=user)
            except LLMClientError as exc:
                last_error = exc
        assert last_error is not None
        raise LLMClientError(
            f"{last_error} (after {attempts} attempt{'s' if attempts != 1 else ''})"
        ) from last_error

    def _chat_once(self, role: str, *, system: str, user: str) -> str:
        max_tokens = (
            self._settings.llm_max_tokens_code
            if role in _CODE_ROLES
            else self._settings.llm_max_tokens_plan
        )
        try:
            response = self._client.chat.completions.create(
                model=self.model_for(role),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                # Some routes behind a combo turn out to be full agent runtimes
                # (they'll offer tools like Read/Bash even though we never sent
                # any) rather than plain chat-completion models. tool_choice
                # "none" tells any OpenAI-compatible backend not to call tools
                # even if it wants to, so we always get a text reply back.
                tool_choice="none",
                # Generation time scales with output length; plans are short
                # by design, so a cap cuts latency and spend. Code roles get a
                # much larger budget for whole-file replies.
                max_tokens=max_tokens,
            )
        except APIError as exc:
            # Covers connection failures, timeouts, and non-2xx responses
            # (rate limits, provider errors, etc.) in one place - a raw crash
            # here previously left the user staring at a Python traceback.
            raise LLMClientError(
                f"9router request failed for role {role!r}: {exc}\n"
                "Check that 9router is running and that NINEROUTER_BASE_URL in .env "
                "matches its real API path (its dashboard usually shows this under "
                "an API keys / getting-started panel)."
            ) from exc

        choices = response.choices
        if not choices:
            # This specific 9router combo wraps the real OpenAI-shaped payload
            # one level deeper, inside a "data" envelope, while the top-level
            # response looks like an empty/error shell (choices: null, model:
            # null, etc.) - confirmed by inspecting a live response where the
            # actual generated text was sitting under response.data.choices.
            # The openai SDK's response model allows unrecognised extra
            # fields through as plain dicts, so this is a real (if unusual)
            # place to look before giving up.
            inner = getattr(response, "data", None)
            if isinstance(inner, dict):
                choices = inner.get("choices")

        if not choices:
            raise LLMClientError(
                f"9router returned no choices at all for role {role!r}. Raw response: "
                f"{response.model_dump_json()[:1000]}"
            )

        first = choices[0]
        # `first` is either an SDK object (top-level path) or a plain dict
        # (unwrapped from the "data" envelope) - normalise access either way.
        if isinstance(first, dict):
            content = first.get("message", {}).get("content")
            finish_reason = first.get("finish_reason")
        else:
            content = first.message.content
            finish_reason = first.finish_reason

        if not content:
            raise LLMClientError(
                f"9router returned an empty reply for role {role!r} "
                f"(finish_reason={finish_reason!r}). If this keeps happening, the model "
                "behind your combo may be an agentic backend (e.g. a Claude Code-style "
                "session) rather than a plain chat model - try a different route/model "
                "for this role in the 9router dashboard."
            )
        return content
