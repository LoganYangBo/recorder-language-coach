"""User settings, kept in coach.db (and edited on the Settings page)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields

from .prompts import PROMPT_VERSION

GOALS = ("work", "daily", "exam")  # work meetings / everyday speech / exams


@dataclass
class Settings:
    goal: str = "work"
    personas: list[str] = field(default_factory=lambda: ["jobs", "obama"])
    max_expressions: int = 10  # per day
    max_pronunciation: int = 10  # per day
    language: str = "en"  # the language being learnt (only "en" in v1)
    explain_in: str = "zh"  # the language explanations are written in
    model: str = "qwen3:14b"
    ollama_url: str = "http://localhost:11434"
    num_ctx: int = 8192  # the model's context window; more needs more VRAM
    ui_lang: str = ""  # "", "en" or "zh"; "" follows the browser

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        known = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in data.items() if k in known})
        s.validate()
        return s

    def validate(self) -> None:
        if self.goal not in GOALS:
            raise ValueError(f"goal must be one of {', '.join(GOALS)}")
        if not isinstance(self.personas, list):
            raise ValueError("personas must be a list")
        for name in ("max_expressions", "max_pronunciation", "num_ctx"):
            value = getattr(self, name)
            if not isinstance(value, int) or value < (512 if name == "num_ctx" else 0):
                raise ValueError(f"{name} must be a whole number")
        if not self.model:
            raise ValueError("model is required")

    def to_dict(self) -> dict:
        return asdict(self)

    def fingerprint(self) -> str:
        """What changes the analysis of a recording: a report made with another
        fingerprint is out of date (it can be regenerated)."""
        key = {
            "v": PROMPT_VERSION,
            "goal": self.goal,
            "personas": sorted(self.personas),
            "language": self.language,
            "explain_in": self.explain_in,
            "model": self.model,
        }
        return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:12]


def load(store) -> Settings:
    return Settings.from_dict(store.get_settings())


def save(store, settings: Settings) -> None:
    settings.validate()
    store.put_settings(settings.to_dict())
