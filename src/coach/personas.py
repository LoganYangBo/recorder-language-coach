"""Style cards of famous speakers, one Markdown file each.

    ---
    name: Steve Jobs
    name_zh: 史蒂夫·乔布斯
    gender: male
    tags: work, presentation
    ---
    - Short, declarative sentences. ...

The body describes how the person talks (not what they think); the model
uses it to say the user's sentence the way they might. Built-in cards live
in coach/persona_cards/; files in a `personas` folder next to coach.db add to
them or replace one with the same file name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path


@dataclass
class Persona:
    key: str  # file name without .md
    name: str
    name_zh: str
    gender: str  # male / female
    tags: list[str] = field(default_factory=list)
    style: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "name_zh": self.name_zh,
            "gender": self.gender,
            "tags": self.tags,
            "style": self.style,
        }


def parse(key: str, text: str) -> Persona:
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        head, _, body = text[3:].partition("\n---")
        for line in head.strip().splitlines():
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return Persona(
        key=key,
        name=meta.get("name", key),
        name_zh=meta.get("name_zh", meta.get("name", key)),
        gender=meta.get("gender", ""),
        tags=[t.strip() for t in meta.get("tags", "").split(",") if t.strip()],
        style=body.strip(),
    )


def load(extra_dirs: list[Path] | None = None) -> dict[str, Persona]:
    """All personas by key, built-in first, then each extra folder's."""
    out: dict[str, Persona] = {}
    builtin = resources.files("coach").joinpath("persona_cards")
    for f in sorted(builtin.iterdir(), key=lambda f: f.name):
        if f.name.endswith(".md"):
            out[f.name[:-3]] = parse(f.name[:-3], f.read_text(encoding="utf-8"))
    for d in extra_dirs or []:
        if d.is_dir():
            for f in sorted(d.glob("*.md")):
                out[f.stem] = parse(f.stem, f.read_text(encoding="utf-8"))
    return out
