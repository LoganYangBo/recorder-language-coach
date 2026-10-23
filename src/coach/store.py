"""coach.db: what the coach found in each recording, the user's corpus and settings.

Kept apart from recorder.db, which the coach only reads (through the SDK's
Recorder API). Recordings are referred to by their recorder.db id.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

# Each entry upgrades the schema by one version; PRAGMA user_version records
# how many have run. Append new migrations, never edit old ones.
MIGRATIONS = [
    """
    CREATE TABLE settings (
        key    TEXT PRIMARY KEY,
        value  TEXT NOT NULL  -- JSON
    );
    -- one row per recording the coach has looked at
    CREATE TABLE analyses (
        recording_id  INTEGER PRIMARY KEY,
        day           TEXT,             -- YYYY-MM-DD of the recording's start, NULL if undated
        status        TEXT NOT NULL,    -- done / failed / empty (nothing in the target language)
        model         TEXT,
        settings      TEXT,             -- Settings.fingerprint() used
        sentences     INTEGER NOT NULL DEFAULT 0,  -- my sentences analysed
        error         TEXT,
        attempts      INTEGER NOT NULL DEFAULT 0,
        seconds       REAL,             -- how long the model took
        updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX idx_analyses_day ON analyses(day);
    -- F2: a sentence of mine said better
    CREATE TABLE expressions (
        id            INTEGER PRIMARY KEY,
        recording_id  INTEGER NOT NULL,
        day           TEXT,
        start         REAL NOT NULL,
        end           REAL NOT NULL,
        said          TEXT NOT NULL,
        better        TEXT NOT NULL,
        kind          TEXT NOT NULL,    -- grammar / word / natural / wordy
        why           TEXT NOT NULL,
        importance    INTEGER NOT NULL, -- 1..5
        dismissed     INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_expressions_day ON expressions(day);
    -- how the chosen famous people would have said it
    CREATE TABLE persona_lines (
        expression_id  INTEGER NOT NULL REFERENCES expressions(id) ON DELETE CASCADE,
        persona        TEXT NOT NULL,   -- persona key, e.g. "jobs"
        line           TEXT NOT NULL,
        note           TEXT NOT NULL
    );
    CREATE INDEX idx_persona_lines ON persona_lines(expression_id);
    -- F3: words Whisper wasn't sure of, with a tip
    CREATE TABLE pronunciation (
        id            INTEGER PRIMARY KEY,
        recording_id  INTEGER NOT NULL,
        day           TEXT,
        word_id       INTEGER NOT NULL, -- recorder.db word id
        word          TEXT NOT NULL,
        ipa           TEXT,
        probability   REAL NOT NULL,
        tip           TEXT NOT NULL,
        start         REAL NOT NULL,
        end           REAL NOT NULL
    );
    CREATE INDEX idx_pronunciation_day ON pronunciation(day);
    -- F5: things the other person said that I probably didn't follow
    CREATE TABLE knowledge (
        id            INTEGER PRIMARY KEY,
        recording_id  INTEGER NOT NULL,
        day           TEXT,
        term          TEXT NOT NULL,
        category      TEXT NOT NULL,
        explanation   TEXT NOT NULL,
        evidence      TEXT NOT NULL,    -- why the coach thinks I missed it
        quote         TEXT NOT NULL,
        speaker       TEXT,
        start         REAL NOT NULL,
        end           REAL NOT NULL
    );
    CREATE INDEX idx_knowledge_day ON knowledge(day);
    -- terms the user said they know: never brought up again
    CREATE TABLE known_terms (
        term  TEXT PRIMARY KEY  -- lower case
    );
    -- expressions worth keeping, suggested by the coach
    CREATE TABLE suggestions (
        id            INTEGER PRIMARY KEY,
        recording_id  INTEGER NOT NULL,
        day           TEXT,
        phrase        TEXT NOT NULL,
        meaning       TEXT NOT NULL,
        example       TEXT NOT NULL,
        source        TEXT NOT NULL,    -- me / other
        start         REAL,
        end           REAL
    );
    CREATE INDEX idx_suggestions_day ON suggestions(day);
    -- F6: the user's corpus
    CREATE TABLE corpus (
        id            INTEGER PRIMARY KEY,
        phrase        TEXT NOT NULL UNIQUE,
        meaning       TEXT NOT NULL DEFAULT '',
        example       TEXT NOT NULL DEFAULT '',
        source        TEXT NOT NULL,    -- expression / persona / other / knowledge / me
        persona       TEXT,
        recording_id  INTEGER,
        day           TEXT,
        start         REAL,
        end           REAL,
        tags          TEXT NOT NULL DEFAULT '[]',  -- JSON list
        created_at    TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    # v2: expressions are found per recording, then the day's top ones are
    # explained ("polished") in a second pass
    """
    ALTER TABLE expressions ADD COLUMN context TEXT NOT NULL DEFAULT '';
    ALTER TABLE expressions ADD COLUMN polished INTEGER NOT NULL DEFAULT 1;
    """,
]
SCHEMA_VERSION = len(MIGRATIONS)

# per-recording results, deleted together when a recording is analysed again
# (pronunciation belongs to the day's polish pass, which tidies it up itself)
RESULT_TABLES = ("expressions", "knowledge", "suggestions")


def _statements(script: str) -> list[str]:
    out, buf = [], ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    return out


class CoachStore:
    def __init__(self, path: str | Path = "coach.db"):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"coach.db schema v{version} is newer than this app (v{SCHEMA_VERSION})"
            )
        with self.conn:
            for v in range(version, SCHEMA_VERSION):
                for stmt in _statements(MIGRATIONS[v]):
                    self.conn.execute(stmt)
                self.conn.execute(f"PRAGMA user_version = {v + 1}")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _rows(self, sql: str, args=()) -> list[dict]:
        cur = self.conn.execute(sql, args)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # ---- settings ----

    def get_settings(self) -> dict:
        return {k: json.loads(v) for k, v in self.conn.execute("SELECT key, value FROM settings")}

    def put_settings(self, values: dict) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [(k, json.dumps(v, ensure_ascii=False)) for k, v in values.items()],
            )

    # ---- analyses ----

    def analysis(self, recording_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM analyses WHERE recording_id = ?", (recording_id,))
        return rows[0] if rows else None

    def analyses(self, day: str | None = None) -> list[dict]:
        if day is None:
            return self._rows("SELECT * FROM analyses ORDER BY day, recording_id")
        return self._rows("SELECT * FROM analyses WHERE day = ? ORDER BY recording_id", (day,))

    def save_failure(self, recording_id: int, day: str | None, model: str, error: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO analyses(recording_id, day, status, model, error, attempts) "
                "VALUES (?, ?, 'failed', ?, ?, 1) ON CONFLICT(recording_id) DO UPDATE SET "
                "status = 'failed', model = excluded.model, error = excluded.error, "
                "attempts = attempts + 1, updated_at = datetime('now')",
                (recording_id, day, model, error),
            )

    def save_result(self, recording_id: int, day: str | None, result, meta: dict) -> None:
        """Replace everything stored for this recording with `result`
        (an analyze.Result) in one transaction."""
        with self.conn:
            for table in RESULT_TABLES:
                if table == "expressions":
                    self.conn.execute(
                        "DELETE FROM persona_lines WHERE expression_id IN "
                        "(SELECT id FROM expressions WHERE recording_id = ?)",
                        (recording_id,),
                    )
                self.conn.execute(f"DELETE FROM {table} WHERE recording_id = ?", (recording_id,))
            for e in result.expressions:
                self.conn.execute(
                    "INSERT INTO expressions(recording_id, day, start, end, said, better, kind, "
                    "why, importance, context, polished) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                    (recording_id, day, e.start, e.end, e.said, e.better, e.kind, e.why,
                     e.importance, e.context),
                )  # fmt: skip
            self.conn.executemany(
                "INSERT INTO knowledge(recording_id, day, term, category, explanation, evidence, "
                "quote, speaker, start, end) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(recording_id, day, k.term, k.category, k.explanation, k.evidence, k.quote,
                  k.speaker, k.start, k.end) for k in result.knowledge],
            )  # fmt: skip
            self.conn.executemany(
                "INSERT INTO suggestions(recording_id, day, phrase, meaning, example, source, "
                "start, end) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(recording_id, day, s.phrase, s.meaning, s.example, s.source, s.start, s.end)
                 for s in result.suggestions],
            )  # fmt: skip
            status = "done" if result.sentences else "empty"
            self.conn.execute(
                "INSERT INTO analyses(recording_id, day, status, model, settings, sentences, "
                "error, attempts, seconds) VALUES (?, ?, ?, ?, ?, ?, NULL, 0, ?) "
                "ON CONFLICT(recording_id) DO UPDATE SET day = excluded.day, "
                "status = excluded.status, model = excluded.model, settings = excluded.settings, "
                "sentences = excluded.sentences, error = NULL, attempts = 0, "
                "seconds = excluded.seconds, updated_at = datetime('now')",
                (recording_id, day, status, meta.get("model"), meta.get("settings"),
                 result.sentences, meta.get("seconds")),
            )  # fmt: skip

    # ---- the day's polish pass ----

    def save_polish(self, polish) -> None:
        """Store explanations and famous people's lines (an analyze.Polish)."""
        with self.conn:
            for eid, why in polish.why.items():
                self.conn.execute(
                    "UPDATE expressions SET why = ?, polished = 1 WHERE id = ?", (why, eid)
                )
                self.conn.execute("DELETE FROM persona_lines WHERE expression_id = ?", (eid,))
                self.conn.executemany(
                    "INSERT INTO persona_lines(expression_id, persona, line, note) "
                    "VALUES (?, ?, ?, ?)",
                    [(eid, p.persona, p.line, p.note) for p in polish.personas.get(eid, [])],
                )

    def save_pronunciation(self, day: str, rows: list, keep: set[int]) -> None:
        """Add tips (analyze.Pronunciation with recording_id) and drop the day's
        tips for words no longer among `keep` (word ids)."""
        with self.conn:
            for (wid,) in self.conn.execute(
                "SELECT word_id FROM pronunciation WHERE day = ?", (day,)
            ).fetchall():
                if wid not in keep:
                    self.conn.execute(
                        "DELETE FROM pronunciation WHERE day = ? AND word_id = ?", (day, wid)
                    )
            self.conn.executemany(
                "INSERT INTO pronunciation(recording_id, day, word_id, word, ipa, probability, "
                "tip, start, end) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(rid, day, p.word_id, p.word, p.ipa, p.probability, p.tip, p.start, p.end)
                 for rid, p in rows],
            )  # fmt: skip

    # ---- what the day report reads ----

    def expressions(self, day: str) -> list[dict]:
        rows = self._rows(
            "SELECT * FROM expressions WHERE day = ? AND dismissed = 0 "
            "ORDER BY importance DESC, recording_id, start",
            (day,),
        )
        for r in rows:
            r["personas"] = self._rows(
                "SELECT persona, line, note FROM persona_lines WHERE expression_id = ? "
                "ORDER BY rowid",
                (r["id"],),
            )
        return rows

    def pronunciation(self, day: str) -> list[dict]:
        return self._rows(
            "SELECT * FROM pronunciation WHERE day = ? ORDER BY probability, recording_id, start",
            (day,),
        )

    def knowledge(self, day: str) -> list[dict]:
        return self._rows(
            "SELECT * FROM knowledge WHERE day = ? AND lower(term) NOT IN "
            "(SELECT term FROM known_terms) ORDER BY recording_id, start",
            (day,),
        )

    def suggestions(self, day: str) -> list[dict]:
        return self._rows(
            "SELECT * FROM suggestions WHERE day = ? ORDER BY recording_id, start", (day,)
        )

    def days(self) -> list[dict]:
        """Days with at least one analysed recording, newest first, with counts."""
        return self._rows(
            "SELECT a.day, COUNT(*) AS recordings, SUM(a.sentences) AS sentences, "
            "SUM(a.status = 'failed') AS failed, "
            "(SELECT COUNT(*) FROM expressions e WHERE e.day = a.day AND e.dismissed = 0) "
            "  AS expressions "
            "FROM analyses a WHERE a.day IS NOT NULL GROUP BY a.day ORDER BY a.day DESC"
        )

    def dismiss_expression(self, expression_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE expressions SET dismissed = 1 WHERE id = ?", (expression_id,)
            )
        return cur.rowcount > 0

    def mark_known(self, term: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO known_terms(term) VALUES (?)", (term.strip().lower(),)
            )

    def known_terms(self) -> list[str]:
        return [t for (t,) in self.conn.execute("SELECT term FROM known_terms ORDER BY term")]

    # ---- corpus ----

    def add_to_corpus(self, phrase: str, source: str, **fields) -> dict:
        """Add an expression (or update the one with the same text); returns it."""
        phrase = phrase.strip()
        if not phrase:
            raise ValueError("empty phrase")
        keys = ("meaning", "example", "persona", "recording_id", "day", "start", "end")
        values = {k: fields.get(k) for k in keys}
        values["meaning"] = values["meaning"] or ""
        values["example"] = values["example"] or ""
        tags = json.dumps(fields.get("tags") or [], ensure_ascii=False)
        with self.conn:
            self.conn.execute(
                "INSERT INTO corpus(phrase, source, tags, meaning, example, persona, recording_id, "
                "day, start, end) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(phrase) DO UPDATE SET meaning = excluded.meaning, "
                "example = excluded.example",
                (phrase, source, tags, *values.values()),
            )
        return self.corpus_entry(phrase=phrase)

    def corpus_entry(self, entry_id: int | None = None, phrase: str | None = None) -> dict | None:
        if entry_id is not None:
            rows = self._rows("SELECT * FROM corpus WHERE id = ?", (entry_id,))
        else:
            rows = self._rows("SELECT * FROM corpus WHERE phrase = ?", (phrase,))
        if not rows:
            return None
        rows[0]["tags"] = json.loads(rows[0]["tags"])
        return rows[0]

    def corpus(self, query: str = "", source: str | None = None) -> list[dict]:
        where, args = [], []
        if query:
            where.append("(phrase LIKE ? OR meaning LIKE ? OR example LIKE ?)")
            args += [f"%{query}%"] * 3
        if source:
            where.append("source = ?")
            args.append(source)
        sql = "SELECT * FROM corpus" + (f" WHERE {' AND '.join(where)}" if where else "")
        rows = self._rows(sql + " ORDER BY created_at DESC, id DESC", args)
        for r in rows:
            r["tags"] = json.loads(r["tags"])
        return rows

    def remove_from_corpus(self, entry_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute("DELETE FROM corpus WHERE id = ?", (entry_id,))
        return cur.rowcount > 0
