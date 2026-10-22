"""The language model behind the coach: Ollama on this computer.

Anything with `chat(system, user, schema) -> dict` works, so another model
(or a fake one, in tests) can stand in.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class LLMError(RuntimeError):
    pass


class Ollama:
    def __init__(
        self,
        model: str = "qwen3:14b",
        url: str = "http://localhost:11434",
        num_ctx: int = 8192,
        timeout: float = 900,
        keep_alive: int | str = 0,  # unload when done, so Whisper has the GPU back
    ):
        self.model = model
        self.url = url.rstrip("/")
        self.num_ctx = num_ctx
        self.timeout = timeout
        self.keep_alive = keep_alive
        self._think = True  # send think: false (Qwen3 and other thinking models)

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                return json.loads(res.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            try:
                detail = json.loads(detail).get("error", detail)
            except ValueError:
                pass
            raise LLMError(f"Ollama {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise LLMError(f"can't reach Ollama at {self.url} ({e.reason}); is it running?") from e

    def chat(self, system: str, user: str, schema: dict) -> dict:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": schema,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0.2, "num_ctx": self.num_ctx},
        }
        if self._think:
            body["think"] = False
        try:
            res = self._post("/api/chat", body)
        except LLMError as e:
            if not self._think or "think" not in str(e):
                raise
            self._think = False  # a model without thinking rejects the option
            body.pop("think")
            res = self._post("/api/chat", body)
        content = res.get("message", {}).get("content", "")
        try:
            return json.loads(content)
        except ValueError as e:
            raise LLMError(f"the model's answer is not JSON: {content[:200]!r}") from e

    def models(self) -> list[str]:
        """Models pulled into Ollama, for the Settings page."""
        req = urllib.request.Request(self.url + "/api/tags")
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return [m["name"] for m in json.loads(res.read()).get("models", [])]
        except (OSError, ValueError, KeyError):
            return []
