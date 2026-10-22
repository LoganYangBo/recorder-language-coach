"""The coach's local web page and the JSON HTTP API behind it (F8).

The page only talks to /api/..., so a phone app can use the same API.
"""

from __future__ import annotations

import json
import mimetypes
import queue
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .coach import Coach
from .settings import GOALS, Settings, save

AUDIO_TYPES = {".ogg": "audio/ogg", ".opus": "audio/ogg", ".m4a": "audio/mp4", ".wav": "audio/wav"}
SOURCES = ("expression", "persona", "other", "knowledge", "me")
DAY = r"(\d{4}-\d{2}-\d{2})"


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class App:
    """API handlers: (query, body, *path args) -> JSON-serialisable."""

    def __init__(self, coach: Coach, log=print):
        self.coach = coach
        self.log = log
        self.jobs: queue.Queue = queue.Queue()
        self.queued: list[int] = []
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        while True:
            rid = self.jobs.get()
            try:
                r = self.coach.run_recording(rid)
                self.log(f"recording {rid}: {r['expressions']} expressions, {r['seconds']} s")
            except Exception as e:
                self.log(f"recording {rid} failed: {type(e).__name__}: {e}")
            finally:
                if rid in self.queued:
                    self.queued.remove(rid)

    def _enqueue(self, ids: list[int]) -> dict:
        new = [rid for rid in ids if rid not in self.queued]
        for rid in new:
            self.queued.append(rid)
            self.jobs.put(rid)
        return {"queued": len(new)}

    # ---- status and days ----

    def status(self, q, body):
        return {"current": self.coach.current, "queued": list(self.queued)}

    def days(self, q, body):
        analysed = {d["day"]: d for d in self.coach.store.days()}
        with self.coach.recorder() as rec:
            recorded = {d.date.isoformat(): d for d in rec.days()}
        out = []
        for day in sorted(set(analysed) | set(recorded), reverse=True):
            a = analysed.get(day, {})
            out.append({
                "day": day,
                "recordings": recorded[day].recordings if day in recorded else 0,
                "analysed": a.get("recordings", 0),
                "expressions": a.get("expressions", 0),
            })  # fmt: skip
        return out

    def day(self, q, body, day: str):
        return self.coach.day_report(day)

    def run_day(self, q, body, day: str):
        return self._enqueue(self.coach.recordings_on(day))

    def run_recording(self, q, body, rid: int):
        return self._enqueue([rid])

    # ---- acting on the report ----

    def dismiss(self, q, body, eid: int):
        if not self.coach.store.dismiss_expression(eid):
            raise ApiError(404, f"no expression {eid}")
        return {"ok": True}

    def known(self, q, body):
        term = (body.get("term") or "").strip()
        if not term:
            raise ApiError(400, "term is required")
        self.coach.store.mark_known(term)
        return {"ok": True}

    # ---- corpus ----

    def corpus(self, q, body):
        source = q.get("source", [""])[0] or None
        return self.coach.store.corpus(q.get("q", [""])[0].strip(), source)

    def corpus_add(self, q, body):
        source = body.get("source", "me")
        if source not in SOURCES:
            raise ApiError(400, f"source must be one of {', '.join(SOURCES)}")
        try:
            return self.coach.store.add_to_corpus(
                body.get("phrase") or "",
                source,
                **{
                    k: body.get(k)
                    for k in (
                        "meaning",
                        "example",
                        "persona",
                        "recording_id",
                        "day",
                        "start",
                        "end",
                        "tags",
                    )
                },
            )
        except ValueError as e:
            raise ApiError(400, str(e)) from e

    def corpus_remove(self, q, body, cid: int):
        if not self.coach.store.remove_from_corpus(cid):
            raise ApiError(404, f"no entry {cid}")
        return {"ok": True}

    def corpus_markdown(self) -> bytes:
        lines = [
            "# Corpus",
            "",
            "| Phrase | Meaning | Example | Source | Day |",
            "|---|---|---|---|---|",
        ]
        for e in self.coach.store.corpus():
            cells = [
                e["phrase"],
                e["meaning"],
                e["example"],
                e["persona"] or e["source"],
                e["day"] or "",
            ]
            lines.append(
                "| "
                + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in cells)
                + " |"
            )
        return ("\n".join(lines) + "\n").encode()

    # ---- settings ----

    def settings(self, q, body):
        s = self.coach.settings
        return {
            "settings": s.to_dict(),
            "goals": list(GOALS),
            "personas": [p.to_dict() for p in self.coach.personas().values()],
            "models": self.coach.llm(s).models() if hasattr(self.coach.llm(s), "models") else [],
            "recorder_db": self.coach.recorder_db,
            "coach_db": str(self.coach.coach_db),
        }

    def save_settings(self, q, body):
        try:
            s = Settings.from_dict({**self.coach.settings.to_dict(), **body})
        except (TypeError, ValueError) as e:
            raise ApiError(400, str(e)) from e
        unknown = [k for k in s.personas if k not in self.coach.personas()]
        if unknown:
            raise ApiError(400, f"unknown persona: {', '.join(unknown)}")
        save(self.coach.store, s)
        return self.settings(q, {})

    # ---- audio ----

    def audio_path(self, rid: int) -> Path:
        with self.coach.recorder() as rec:
            r = next((r for r in rec.recordings() if r.id == rid), None)
        if r is None:
            raise ApiError(404, f"no recording {rid}")
        path = Path(r.path)
        if not path.is_absolute():
            path = Path(self.coach.recorder_db).resolve().parent / path
        if not path.is_file():
            raise ApiError(404, f"audio file not found: {r.path}")
        return path


ROUTES = [  # (method, path, handler)
    ("GET", r"/api/status", "status"),
    ("GET", r"/api/days", "days"),
    ("GET", rf"/api/days/{DAY}", "day"),
    ("POST", rf"/api/days/{DAY}/run", "run_day"),
    ("POST", r"/api/recordings/(\d+)/run", "run_recording"),
    ("POST", r"/api/expressions/(\d+)/dismiss", "dismiss"),
    ("POST", r"/api/known", "known"),
    ("GET", r"/api/corpus", "corpus"),
    ("POST", r"/api/corpus", "corpus_add"),
    ("DELETE", r"/api/corpus/(\d+)", "corpus_remove"),
    ("GET", r"/api/settings", "settings"),
    ("POST", r"/api/settings", "save_settings"),
]


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep the console quiet
            pass

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, data):
            body = json.dumps(data, ensure_ascii=False).encode()
            self._send(status, body, "application/json; charset=utf-8")

        def _dispatch(self):
            url = urlparse(self.path)
            if self.command == "GET" and url.path in ("/", "/index.html"):
                html = resources.files("coach").joinpath("web.html").read_bytes()
                return self._send(200, html, "text/html; charset=utf-8")
            if self.command == "GET" and url.path == "/api/corpus.md":
                return self._send(
                    200,
                    app.corpus_markdown(),
                    "text/markdown; charset=utf-8",
                    {"Content-Disposition": "attachment; filename=corpus.md"},
                )
            m = re.fullmatch(r"/api/recordings/(\d+)/audio", url.path)
            if self.command in ("GET", "HEAD") and m:
                return self._audio(app.audio_path(int(m.group(1))))
            for method, pattern, name in ROUTES:
                m = re.fullmatch(pattern, url.path)
                if m and method == self.command:
                    body = {}
                    if self.command == "POST":
                        n = int(self.headers.get("Content-Length") or 0)
                        try:
                            body = json.loads(self.rfile.read(n) or b"{}")
                        except ValueError as e:
                            raise ApiError(400, "body is not JSON") from e
                    args = [int(g) if g.isdigit() else unquote(g) for g in m.groups()]
                    return self._json(200, getattr(app, name)(parse_qs(url.query), body, *args))
            raise ApiError(404, "not found")

        def _audio(self, path: Path):
            size = path.stat().st_size
            ctype = AUDIO_TYPES.get(path.suffix.lower()) or (
                mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            )
            start, end, status = 0, size - 1, 200
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                else:  # suffix range: last N bytes
                    start = max(0, size - int(m.group(2)))
                if start > end:
                    return self._send(416, b"", ctype, {"Content-Range": f"bytes */{size}"})
                status = 206
            with open(path, "rb") as f:
                f.seek(start)
                data = f.read(end - start + 1)
            extra = {"Accept-Ranges": "bytes"}
            if status == 206:
                extra["Content-Range"] = f"bytes {start}-{end}/{size}"
            self._send(status, data, ctype, extra)

        def _handle(self):
            try:
                self._dispatch()
            except ApiError as e:
                self._json(e.status, {"error": str(e)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:  # report instead of dropping the connection
                self._json(500, {"error": f"{type(e).__name__}: {e}"})

        do_GET = do_POST = do_DELETE = do_HEAD = _handle

    return Handler


def serve(
    coach: Coach,
    host: str = "127.0.0.1",
    port: int = 8766,
    open_browser: bool = True,
    watch: float | None = 300,
) -> None:
    app = App(coach)
    if watch:
        coach.start_watching(watch)
        print(f"analysing new recordings every {int(watch)} s")
    server = ThreadingHTTPServer((host, port), make_handler(app))
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"language coach on {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
