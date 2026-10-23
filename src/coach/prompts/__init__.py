"""Prompts, one folder per language being learnt (prompts/en/ ...)."""

from __future__ import annotations

from importlib import resources

# bump when a prompt changes, so reports made with the old one show as out of date
PROMPT_VERSION = 2


def load(language: str, name: str) -> str:
    path = resources.files("coach").joinpath("prompts", language, f"{name}.md")
    if not path.is_file():
        raise ValueError(f"no {name} prompt for language {language!r}")
    return path.read_text(encoding="utf-8")
