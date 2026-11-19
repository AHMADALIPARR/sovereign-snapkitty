"""Language-model adapters.

A LM is anything callable with a list of ``{"role": ..., "content": ...}``
messages that returns the model's reply as a string. The harness is written
against this one interface, so swapping the model means swapping the
adapter, not the programs.

Adapters shipped:
- :class:`FakeLM` — deterministic, substring-matched canned replies.
  No network, no key. This is what the tests and the offline harness use.
- :class:`OllamaLM` — a local Ollama server (``http://localhost:11434``).
  The real-model path for this user's own hardware. Fails cleanly with a
  clear error when Ollama is not running.
- :class:`OpenAILM` — OpenAI chat completions. Needs ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


Message = dict  # {"role": str, "content": str}


class BaseLM:
    """A language model. Subclasses implement :meth:`__call__`."""

    name: str = "base"

    def __call__(self, messages: list[Message]) -> str:
        raise NotImplementedError

    def prompt_text(self, messages: list[Message]) -> str:
        """Flatten messages to one text block (for matching / logging)."""
        return "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages
        )


class FakeLM(BaseLM):
    """Deterministic fake LM for tests and offline runs.

    Give it ``responses`` mapping a substring to a reply. The first
    substring found in the flattened prompt wins. Anything unmatched gets
    ``default``. Because the reply is a pure function of the prompt, runs
    are reproducible and the harness can be verified without a model.
    """

    name = "fake"

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        default: str = "Answer: done.",
    ) -> None:
        self.responses = dict(responses or {})
        self.default = default
        self.calls: list[list[Message]] = []

    def __call__(self, messages: list[Message]) -> str:
        self.calls.append(list(messages))
        text = self.prompt_text(messages)
        # Longest needle first: the most specific pattern wins. This matters
        # for ReAct, where the prompt accumulates history — a later
        # observation ("Observation: 4") must beat the original question
        # ("2+2") that is still in the transcript.
        for needle, reply in sorted(
            self.responses.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            if needle in text:
                return reply
        return self.default


def _post_json(url: str, payload: dict, timeout: float = 120.0) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class OllamaLM(BaseLM):
    """A local Ollama model, via its ``/api/chat`` endpoint.

    Example: ``OllamaLM("llama3.2")`` talks to the Ollama server on
    ``http://localhost:11434``. Raises a clear ``ConnectionError`` when the
    server is not reachable, so the harness reports it instead of hanging.
    """

    name = "ollama"

    def __init__(self, model: str, host: str = "http://localhost:11434") -> None:
        self.model = model
        self.host = host.rstrip("/")

    def __call__(self, messages: list[Message]) -> str:
        try:
            out = _post_json(
                f"{self.host}/api/chat",
                {
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                },
            )
        except urllib.error.URLError as exc:
            raise ConnectionError(
                f"Ollama server not reachable at {self.host} "
                f"(is Ollama running? {exc.reason})"
            ) from exc
        msg = out.get("message") or {}
        return str(msg.get("content", ""))


class OpenAILM(BaseLM):
    """OpenAI chat completions. Needs ``OPENAI_API_KEY`` in the environment
    (or passed as ``api_key``)."""

    name = "openai"

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not self.api_key:
            raise ValueError("OpenAILM needs OPENAI_API_KEY (env or api_key=)")

    def __call__(self, messages: list[Message]) -> str:
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(
                {"model": self.model, "messages": messages}
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120.0) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"OpenAI request failed ({exc.code}): {body}") from exc
        choices = out.get("choices") or []
        if not choices:
            return ""
        return str(choices[0].get("message", {}).get("content", ""))
