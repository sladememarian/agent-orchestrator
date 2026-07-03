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

from openai import APIConnectionError, OpenAI

from .config import Settings

# Role name -> the Settings field holding that role's model string.
_ROLE_MODEL_FIELD = {
    "head": "model_head",
    "developer": "model_developer",
    "realtime_fixer": "model_realtime_fixer",
    "makefile_tester": "model_makefile_tester",
}


class LLMClientError(RuntimeError):
    """Raised when 9router can't be reached or rejects a request outright."""


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = OpenAI(base_url=settings.ninerouter_base_url, api_key=settings.ninerouter_api_key)

    def model_for(self, role: str) -> str:
        field = _ROLE_MODEL_FIELD.get(role)
        if field is None:
            raise LLMClientError(f"Unknown agent role {role!r}; add it to _ROLE_MODEL_FIELD")
        return getattr(self._settings, field)

    def chat(self, role: str, *, system: str, user: str) -> str:
        """One request/response call for the given role. Returns the reply text."""
        try:
            response = self._client.chat.completions.create(
                model=self.model_for(role),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except APIConnectionError as exc:
            raise LLMClientError(
                f"Couldn't reach 9router at {self._settings.ninerouter_base_url}: {exc}\n"
                "Check that 9router is running and that NINEROUTER_BASE_URL in .env "
                "matches its real API path (its dashboard usually shows this under "
                "an API keys / getting-started panel)."
            ) from exc

        choice = response.choices[0].message.content
        if not choice:
            raise LLMClientError(f"9router returned an empty reply for role {role!r}")
        return choice
