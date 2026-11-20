"""Local-only command line interface. JSON output contains no credentials."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .core import CasebookStore, IntegrityError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VOODOO-CASEBOOK governed local memory v0.1")
    parser.add_argument(
        "--data-root", type=Path,
        default=Path.home() / "0_EVIDENCE" / "VOODOO-CASEBOOK",
        help="Private local evidence folder OUTSIDE source repositories",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create empty local journal")
    start = commands.add_parser("start", help="Create session and immutable GOAL event")
    start.add_argument("--project", required=True)
    start.add_argument("--goal", required=True)
    start.add_argument("--session-id")
    append = commands.add_parser("append", help="Append a classified session event")
    append.add_argument("--session", required=True)
    append.add_argument("--type", required=True)
    append.add_argument("--summary", required=True)
    append.add_argument("--source", default="user")
    append.add_argument("--status", default="OPEN")
    append.add_argument("--event-id")
    show = commands.add_parser("show", help="Show session with all attributed events")
    show.add_argument("--session", required=True)
    commands.add_parser("verify", help="Read-only journal physical+hash-chain check")
    args = parser.parse_args(argv)
    try:
        store = CasebookStore(args.data_root)
        if args.command == "init":
            store.initialize()
            result = {"status": "INITIALIZED", "integrity": store.verify()}
        elif args.command == "start":
            sid = store.start_session(
                project=args.project, goal=args.goal, session_id=args.session_id,
            )
            result = {"status": "SESSION_CREATED", "session_id": sid}
        elif args.command == "append":
            result = store.append(
                session_id=args.session, event_type=args.type, summary=args.summary,
                source=args.source, status=args.status, event_id=args.event_id,
            )
        elif args.command == "show":
            result = store.get_session(args.session)
        elif args.command == "verify":
            result = store.verify()
        else:
            raise AssertionError("unreachable command")
    except (ValueError, IntegrityError, LookupError, PermissionError,
            FileNotFoundError, OSError, sqlite3.DatabaseError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
