"""Running the coach: which recordings still need analysing, the analysis
itself, the day report, and the background loop that follows sync (F7)."""

from __future__ import annotations

import threading
import traceback
from pathlib import Path

from recorder import Recorder

from . import personas as personas_mod
from .analyze import analyze
from .llm import Ollama
from .settings import Settings, load
from .source import load_material
from .store import CoachStore

MAX_ATTEMPTS = 3  # a recording that keeps failing is left alone until run by hand


def default_coach_db(recorder_db: str | Path) -> Path:
    return Path(recorder_db).resolve().parent / "coach.db"


class Coach:
    def __init__(self, recorder_db: str | Path, coach_db: str | Path | None = None, llm=None):
        self.recorder_db = str(recorder_db)
        self.coach_db = Path(coach_db) if coach_db else default_coach_db(recorder_db)
        self._local = threading.local()  # one coach.db connection per thread
        self.store  # create / migrate it now
        self._llm = llm  # None: Ollama, as the settings say
        self.lock = threading.Lock()  # one analysis at a time (one GPU)
        self.current: dict | None = None  # what is being analysed now, for the UI

    @property
    def store(self) -> CoachStore:
        if getattr(self._local, "store", None) is None:
            self._local.store = CoachStore(self.coach_db)
        return self._local.store

    def close(self) -> None:
        self.store.close()
        self._local.store = None

    def recorder(self) -> Recorder:
        return Recorder(self.recorder_db)

    @property
    def settings(self) -> Settings:
        return load(self.store)

    def personas(self) -> dict:
        return personas_mod.load([self.coach_db.parent / "personas"])

    def llm(self, settings: Settings):
        if self._llm is not None:
            return self._llm
        return Ollama(settings.model, settings.ollama_url, settings.num_ctx)

    # ---- what to analyse ----

    def pending(self) -> list[int]:
        """Recordings never analysed, or failed fewer than MAX_ATTEMPTS times; newest first."""
        with self.recorder() as rec:
            recs = rec.recordings()
        done = {a["recording_id"]: a for a in self.store.analyses()}
        out = [
            r
            for r in recs
            if r.id not in done
            or (done[r.id]["status"] == "failed" and done[r.id]["attempts"] < MAX_ATTEMPTS)
        ]
        out.sort(key=lambda r: (r.started_at or "", r.id), reverse=True)
        return [r.id for r in out]

    def recordings_on(self, day: str) -> list[int]:
        with self.recorder() as rec:
            return [r.id for r in rec.recordings(day=day)]

    # ---- analysing ----

    def run_recording(self, recording_id: int) -> dict:
        """Analyse one recording now (again, if it was done before)."""
        settings = self.settings
        known = self.personas()
        chosen = [known[k] for k in settings.personas if k in known]
        with self.lock:
            with self.recorder() as rec:
                material = load_material(rec, recording_id, settings.language)
            self.current = {"recording": recording_id, "day": material.day}
            try:
                result = analyze(material, settings, chosen, self.llm(settings))
            except Exception as e:
                self.store.save_failure(
                    recording_id, material.day, settings.model, f"{type(e).__name__}: {e}"
                )
                raise
            finally:
                self.current = None
            self.store.save_result(
                recording_id,
                material.day,
                result,
                {
                    "model": settings.model,
                    "settings": settings.fingerprint(),
                    "seconds": result.seconds,
                },
            )
        return {
            "recording": recording_id,
            "day": material.day,
            "sentences": result.sentences,
            "expressions": len(result.expressions),
            "seconds": result.seconds,
        }

    def run_pending(self, log=print) -> int:
        """Analyse every pending recording; returns how many were done."""
        n = 0
        for rid in self.pending():
            try:
                r = self.run_recording(rid)
                log(f"recording {rid} ({r['day'] or 'undated'}): {r['sentences']} sentences, "
                    f"{r['expressions']} expressions, {r['seconds']} s")  # fmt: skip
                n += 1
            except Exception as e:
                log(f"recording {rid} failed: {type(e).__name__}: {e}")
        return n

    def watch(self, interval: float = 300, log=print, stop: threading.Event | None = None) -> None:
        """Every `interval` seconds, analyse what sync has added since."""
        stop = stop or threading.Event()
        while not stop.is_set():
            try:
                self.run_pending(log)
            except Exception:  # e.g. recorder.db locked or missing: try again next round
                log(traceback.format_exc())
            stop.wait(interval)

    def start_watching(self, interval: float = 300, log=print) -> threading.Event:
        stop = threading.Event()
        threading.Thread(target=self.watch, args=(interval, log, stop), daemon=True).start()
        return stop

    # ---- the day report ----

    def day_report(self, day: str) -> dict:
        settings = self.settings
        names = {k: p.to_dict() for k, p in self.personas().items()}
        analyses = self.store.analyses(day)
        fp = settings.fingerprint()
        in_corpus = {e["phrase"].lower() for e in self.store.corpus()}
        seen: set[str] = set()
        suggestions = []
        for s in self.store.suggestions(day):
            if s["phrase"].lower() not in seen:
                seen.add(s["phrase"].lower())
                suggestions.append({**s, "saved": s["phrase"].lower() in in_corpus})
        expressions = self.store.expressions(day)[: settings.max_expressions]
        for e in expressions:
            for p in e["personas"]:
                p["name"] = names.get(p["persona"], {}).get("name", p["persona"])
                p["name_zh"] = names.get(p["persona"], {}).get("name_zh", p["name"])
        with self.recorder() as rec:
            recs = {r.id: r for r in rec.recordings(day=day)}
        return {
            "day": day,
            "recordings": len(recs),
            "analysed": sum(a["status"] != "failed" for a in analyses),
            "failed": [a for a in analyses if a["status"] == "failed"],
            "pending": [rid for rid in recs if rid not in {a["recording_id"] for a in analyses}],
            "stale": any(a["status"] == "done" and a["settings"] != fp for a in analyses),
            "sentences": sum(a["sentences"] for a in analyses),
            "models": sorted({a["model"] for a in analyses if a["model"]}),
            "titles": {rid: r.name for rid, r in recs.items()},
            "started": {rid: r.started_at for rid, r in recs.items()},
            "expressions": expressions,
            "pronunciation": self.store.pronunciation(day)[: settings.max_pronunciation],
            "knowledge": self.store.knowledge(day),
            "suggestions": suggestions,
        }
