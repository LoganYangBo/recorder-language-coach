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
        keep_alive: int | str = "10m",  # stay loaded through a batch; unload() after it
    ):
        self.model = model
        self.url = url.rstrip("/")
        self.num_ctx = num_ctx
        self.timeout = timeout
        self.keep_alive = keep_alive
        self._think = True  # send think: false (Qwen3 and other thinking models)
        self.last: dict = {}  # timings of the last call, for the log

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

    def chat(self, system: str, user: str, schema: dict | str | None) -> dict:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0.2, "num_ctx": self.num_ctx},
        }
        if schema is not None:
            body["format"] = schema
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
        self.last = _timings(res)
        content = res.get("message", {}).get("content", "")
        try:
            return json.loads(content)
        except ValueError as e:
            raise LLMError(f"the model's answer is not JSON: {content[:200]!r}") from e

    def unload(self) -> None:
        """Free the GPU (for Whisper) once a batch is done."""
        try:
            self._post("/api/generate", {"model": self.model, "keep_alive": 0})
        except LLMError:
            pass

    def loaded(self) -> list[dict]:
        """What `ollama ps` shows: each loaded model and how much of it is on the GPU."""
        req = urllib.request.Request(self.url + "/api/ps")
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                models = json.loads(res.read()).get("models", [])
        except (OSError, ValueError):
            return []
        return [
            {
                "name": m.get("name"),
                "size_gb": round((m.get("size") or 0) / 2**30, 1),
                "gpu": round(100 * (m.get("size_vram") or 0) / m["size"])
                if m.get("size")
                else None,
                "context": m.get("context_length"),
            }
            for m in models
        ]

    def models(self) -> list[str]:
        """Models pulled into Ollama, for the Settings page."""
        req = urllib.request.Request(self.url + "/api/tags")
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return [m["name"] for m in json.loads(res.read()).get("models", [])]
        except (OSError, ValueError, KeyError):
            return []


def _timings(res: dict) -> dict:
    """Ollama's durations (ns) and token counts, in seconds and tokens/s."""
    out_tokens = res.get("eval_count") or 0
    gen = (res.get("eval_duration") or 0) / 1e9
    return {
        "load_s": round((res.get("load_duration") or 0) / 1e9, 1),
        "prompt_tokens": res.get("prompt_eval_count") or 0,
        "prompt_s": round((res.get("prompt_eval_duration") or 0) / 1e9, 1),
        "out_tokens": out_tokens,
        "tok_s": round(out_tokens / gen, 1) if gen else None,
        "total_s": round((res.get("total_duration") or 0) / 1e9, 1),
    }


def describe(t: dict) -> str:
    """One log line from _timings, e.g. 'load 4.1 s, 1500 -> 300 tokens, 35 tok/s'."""
    if not t:
        return ""
    return (
        f"load {t['load_s']} s, {t['prompt_tokens']} -> {t['out_tokens']} tokens, "
        f"{t['tok_s']} tok/s"
    )
