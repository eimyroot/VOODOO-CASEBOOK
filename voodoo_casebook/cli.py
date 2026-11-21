"""Local-only command line interface. JSON output contains no credentials."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from .core import CasebookStore, IntegrityError
from .backups import create_backup, verify_backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VOODOO-CASEBOOK governed local memory v0.1")
    parser.add_argument(
        "--data-root", type=Path,
        default=os.environ.get("VOODOO_CASEBOOK_DATA_ROOT"),
        help="REQUIRED absolute private evidence root (or VOODOO_CASEBOOK_DATA_ROOT)",
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
    commands.add_parser("backup", help="Create verified local SQLite online snapshot")
    backup_verify = commands.add_parser(
        "backup-verify", help="Independently verify a named snapshot",
    )
    backup_verify.add_argument("--backup-id", required=True)
    args = parser.parse_args(argv)
    if args.data_root is None:
        print(json.dumps({
            "status": "BLOCKED",
            "reason": "explicit --data-root or VOODOO_CASEBOOK_DATA_ROOT is required",
        }))
        return 2
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
        elif args.command == "backup":
            result = create_backup(store)
        elif args.command == "backup-verify":
            result = verify_backup(store, args.backup_id)
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
