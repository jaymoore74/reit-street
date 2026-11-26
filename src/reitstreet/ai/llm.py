"""Chat client for a model served on this machine.

mlx_lm.server, Ollama and llama.cpp's llama-server all expose the OpenAI chat completions
API (POST {base}/v1/chat/completions), so one client covers them. The client:

- refuses a server that is not on this machine unless [llm] allow_remote is set, because
  prompts carry warehouse data and unreleased research must not leave the laptop;
- runs deterministically by default (temperature 0) so extraction is repeatable;
- asks for JSON where the backend supports it and always validates what comes back,
  because small models wrap JSON in prose or code fences.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

import requests

from ..core.config import Settings
from ..core.errors import ConfigError

Message = dict[str, Any]

DEFAULT_URLS = {
    "mlx": "http://127.0.0.1:8080",  # mlx_lm.server default
    "ollama": "http://127.0.0.1:11434",  # ollama serve default
    "openai_compat": "http://127.0.0.1:8080",  # llama.cpp llama-server default
}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class LLMError(RuntimeError):
    """The model server failed or returned something unusable."""


@dataclass
class Completion:
    text: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


class ChatClient(Protocol):
    model: str

    def chat(
        self,
        messages: Sequence[Message],
        *,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Completion: ...


class OpenAICompatClient:
    """OpenAI chat completions over HTTP to a local server."""

    def __init__(
        self,
        base_url: str,
        model: str = "",
        *,
        backend: str = "mlx",
        api_key: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout: float = 600.0,
        allow_remote: bool = False,
        session: requests.Session | None = None,
    ):
        host = urlparse(base_url).hostname or ""
        if host not in LOCAL_HOSTS and not allow_remote:
            raise ConfigError(
                f"[llm] base_url {base_url} is not on this machine. Prompts contain warehouse data; "
                "set [llm] allow_remote = true only for a server you control."
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.backend = backend
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.http = session or requests.Session()

    def _payload(
        self,
        messages: Sequence[Message],
        json_schema: dict[str, Any] | None,
        max_tokens: int | None,
        temperature: float | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "messages": list(messages),
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.max_tokens,
            "stream": False,
        }
        if self.model:
            body["model"] = self.model
        if json_schema is not None:
            if self.backend == "ollama":
                # Ollama constrains decoding to the schema (structured outputs)
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "out", "schema": json_schema, "strict": True},
                }
            elif self.backend == "openai_compat":
                # llama.cpp turns a JSON schema into a grammar
                body["response_format"] = {"type": "json_object", "schema": json_schema}
            # mlx_lm.server has no constrained decoding: the prompt carries the schema and
            # parse_json() validates the answer.
        return body

    def chat(
        self,
        messages: Sequence[Message],
        *,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Completion:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        t0 = time.monotonic()
        try:
            r = self.http.post(
                f"{self.base_url}/v1/chat/completions",
                json=self._payload(messages, json_schema, max_tokens, temperature),
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise LLMError(
                f"model server at {self.base_url} did not answer ({e.__class__.__name__}). "
                "Is it running? See docs/mac/README.md, step 'Serve the model'."
            ) from e
        if r.status_code >= 400:
            raise LLMError(f"model server returned HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected reply shape: {str(data)[:300]}") from e
        usage = data.get("usage") or {}
        return Completion(
            text=text,
            model=str(data.get("model") or self.model),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            seconds=time.monotonic() - t0,
            raw=data,
        )


class ScriptedClient:
    """Test double: replies come from a list or a function of the messages. No network."""

    def __init__(self, replies: Sequence[str] | Callable[[Sequence[Message]], str], model: str = "scripted"):
        self.model = model
        self._replies = replies
        self.calls: list[list[Message]] = []

    def chat(
        self,
        messages: Sequence[Message],
        *,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Completion:
        self.calls.append(list(messages))
        if callable(self._replies):
            text = self._replies(messages)
        else:
            i = len(self.calls) - 1
            if i >= len(self._replies):
                raise LLMError("scripted client ran out of replies")
            text = self._replies[i]
        return Completion(text=text, model=self.model)


def client_from_settings(s: Settings, *, model: str | None = None) -> OpenAICompatClient:
    backend = (s.llm_backend or "mlx").strip().lower()
    if backend not in DEFAULT_URLS:
        raise ConfigError(f"[llm] backend {backend!r}: use mlx, ollama or openai_compat")
    name = model if model is not None else s.llm_model
    if backend == "mlx":
        # mlx_lm.server answers with what it was started with (base, adapter or fused) when a
        # request names no model; naming one makes it load that one instead. So send a name
        # only when the caller asked for a specific model.
        from .models import resolve

        name = resolve(model, s.ai_root) if model else ""
    return OpenAICompatClient(
        s.llm_base_url or DEFAULT_URLS[backend],
        name,
        backend=backend,
        api_key=s.llm_api_key,
        temperature=s.llm_temperature,
        max_tokens=s.llm_max_tokens,
        timeout=s.llm_timeout_seconds,
        allow_remote=s.llm_allow_remote,
    )


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def parse_json(text: str) -> Any:
    """The first JSON object or array in a model reply.

    Strips reasoning blocks (<think>...</think>) and code fences, then decodes from the first
    { or [. Raises LLMError when there is none, so callers can count the failure.
    """
    t = _THINK.sub("", text).strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    dec = json.JSONDecoder()
    for i, ch in enumerate(t):
        if ch in "{[":
            try:
                obj, _ = dec.raw_decode(t[i:])
                return obj
            except json.JSONDecodeError:
                continue
    raise LLMError(f"no JSON in model reply: {text[:200]!r}")
