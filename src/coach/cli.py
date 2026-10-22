"""`coach` command line: run, watch, ui, personas."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .coach import Coach


def _coach(args) -> Coach:
    db = Path(args.db)
    if not db.is_file():
        sys.exit(f"recorder database not found: {db} (use --db)")
    return Coach(db, args.coach_db)


def cmd_run(args) -> None:
    c = _coach(args)
    if args.recording:
        ids = args.recording
    elif args.day:
        ids = c.recordings_on(args.day)
    else:
        n = c.run_pending()
        print(f"{n} recordings analysed")
        return
    for rid in ids:
        try:
            r = c.run_recording(rid)
            print(f"recording {rid}: {r['sentences']} sentences, "
                  f"{r['expressions']} expressions, {r['seconds']} s")  # fmt: skip
        except Exception as e:
            print(f"recording {rid} failed: {type(e).__name__}: {e}")


def cmd_watch(args) -> None:
    c = _coach(args)
    print(f"analysing new recordings every {int(args.interval)} s  (Ctrl+C to stop)")
    try:
        c.watch(args.interval)
    except KeyboardInterrupt:
        pass


def cmd_ui(args) -> None:
    from .web import serve

    serve(
        _coach(args),
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
        watch=None if args.no_watch else args.interval,
    )


def cmd_personas(args) -> None:
    from . import personas

    extra = [Path(args.coach_db).parent / "personas"] if args.coach_db else []
    for p in personas.load(extra).values():
        print(f"{p.key:14} {p.gender:7} {p.name} / {p.name_zh}  [{', '.join(p.tags)}]")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="coach", description="Daily feedback on your spoken English.")
    ap.add_argument(
        "--db", default="recorder.db", help="recorder-sdk database (default: recorder.db)"
    )
    ap.add_argument("--coach-db", help="coach database (default: coach.db next to --db)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="analyse pending recordings now (or a day / recordings again)")
    p.add_argument("--day", help="YYYY-MM-DD: analyse every recording of that day again")
    p.add_argument("--recording", type=int, nargs="+", help="recording ids to analyse again")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("watch", help="keep analysing what sync adds")
    p.add_argument(
        "--interval", type=float, default=300, help="seconds between checks (default 300)"
    )
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("ui", help="local web page (also watches, unless --no-watch)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument(
        "--interval", type=float, default=300, help="seconds between checks (default 300)"
    )
    p.add_argument(
        "--no-watch", action="store_true", help="don't analyse new recordings in the background"
    )
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_ui)

    p = sub.add_parser("personas", help="list the famous people you can pick")
    p.set_defaults(func=cmd_personas)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
