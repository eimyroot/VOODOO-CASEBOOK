"""VOODOO-CASEBOOK v0.1: local-only, append-only SQLite event journal.

The database is evidence, never authorization or independent proof of a claim.
No provider/network calls, project edits, or implicit canonical promotion.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "voodoo-casebook/event/v1"
SCHEMA_VERSION = 1
GENESIS_HASH = "0" * 64
DATABASE_FILENAME = "casebook.sqlite3"
EVENT_TYPES = frozenset({
    "GOAL", "CONTEXT", "IDEA", "DECISION", "ISSUE", "TODO",
    "COMMAND_PLAN", "COMMAND_RESULT", "EVIDENCE", "LINK", "BLOCKER",
    "RISK", "LEARNING", "RESULT", "CANONICAL_CANDIDATE",
})
SOURCES = frozenset({"user", "assistant", "tool", "system"})
STATUSES = frozenset({"OPEN", "RESOLVED", "SUPERSEDED", "CANDIDATE"})


class IntegrityError(RuntimeError):
    """Stored state cannot safely be treated as an intact CASEBOOK journal."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _text(value: str, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError("{} must be nonempty text of at most {} characters".format(field, limit))
    return value


def _event_digest(event: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(event).encode("utf-8")).hexdigest()


def _safe_json(value: Any, field: str, max_bytes: int) -> str:
    try:
        data = canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("{} must contain JSON-compatible data".format(field)) from exc
    if len(data.encode("utf-8")) > max_bytes:
        raise ValueError("{} exceeds size limit".format(field))
    return data


def _validate_references(refs: Any) -> list[str]:
    if not isinstance(refs, list) or len(refs) > 32:
        raise ValueError("evidence_refs must be a list of at most 32 references")
    return [_text(ref, "evidence reference", 1024) for ref in refs]


def _validate_metadata(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, dict) or any(not isinstance(k, str) for k in metadata):
        raise ValueError("metadata must be a JSON object with string keys")
    _safe_json(metadata, "metadata", 8192)
    return metadata


def _payload(*, seq: int, event_id: str, session_id: str, project: str,
             timestamp: str, event_type: str, summary: str, source: str,
             evidence_refs: list[str], status: str, metadata: dict[str, Any],
             prev_hash: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "seq": seq,
        "event_id": event_id,
        "session_id": session_id,
        "project": project,
        "timestamp": timestamp,
        "type": event_type,
        "summary": summary,
        "source": source,
        "evidence_refs": evidence_refs,
        "status": status,
        "metadata": metadata,
        "prev_hash": prev_hash,
    }


class CasebookStore:
    """Single-file journal inside a private evidence directory, never a source repository."""

    def __init__(self, data_root: Path) -> None:
        self.root = Path(data_root).expanduser()
        if not self.root.is_absolute() or ".." in self.root.parts:
            raise ValueError("data root must be an absolute path without traversal")
        self.database = self.root / DATABASE_FILENAME

    def _guard(self, *, create: bool) -> None:
        source_root = Path(__file__).resolve().parent.parent
        requested = self.root.absolute()
        if os.path.commonpath((str(requested), str(source_root))) == str(source_root):
            raise ValueError("runtime data must be stored outside the source repository")
        if self.root.is_symlink():
            raise ValueError("data root must not be a symlink")
        if not self.root.exists():
            if not create:
                raise FileNotFoundError("casebook data root does not exist")
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.root.is_dir() or requested != self.root.resolve():
            raise ValueError("data root is not a safe real directory")
        if stat.S_IMODE(self.root.stat().st_mode) & 0o077:
            raise PermissionError("casebook data root must not be accessible by group or others")
        if self.database.is_symlink():
            raise ValueError("database must not be a symlink")
        if self.database.exists() and not self.database.is_file():
            raise ValueError("database path is not a regular file")

    def _connect(self, *, write: bool) -> sqlite3.Connection:
        self._guard(create=write)
        if write:
            db = sqlite3.connect(str(self.database), timeout=10, isolation_level=None)
        else:
            if not self.database.is_file():
                raise FileNotFoundError("casebook database has not been initialized")
            # A consistent SQLite backup of a WAL database can retain the WAL
            # header without WAL sidecars. Immutable read opens that snapshot
            # without creating files; a live WAL journal uses normal ro mode.
            wal = self.database.with_name(self.database.name + "-wal")
            ro_uri = self.database.as_uri() + "?mode=ro"
            if not wal.exists():
                ro_uri += "&immutable=1"
            db = sqlite3.connect(ro_uri, timeout=10, isolation_level=None, uri=True)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        if write:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            os.chmod(str(self.database), 0o600)
        else:
            db.execute("PRAGMA query_only=ON")
        return db

    def initialize(self) -> None:
        with closing(self._connect(write=True)) as db:
            try:
                db.executescript("""BEGIN IMMEDIATE;

                    CREATE TABLE IF NOT EXISTS meta (
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        schema_version INTEGER NOT NULL,
                        head_seq INTEGER NOT NULL CHECK(head_seq >= 0),
                        head_hash TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY,
                        project TEXT NOT NULL,
                        goal TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS events (
                        seq INTEGER PRIMARY KEY CHECK(seq > 0),
                        event_id TEXT NOT NULL UNIQUE,
                        session_id TEXT NOT NULL REFERENCES sessions(session_id),
                        project TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        type TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        source TEXT NOT NULL,
                        evidence_refs_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        metadata_json TEXT NOT NULL,
                        prev_hash TEXT NOT NULL,
                        event_hash TEXT NOT NULL
                    );
                    CREATE TRIGGER IF NOT EXISTS events_no_update
                    BEFORE UPDATE ON events BEGIN
                        SELECT RAISE(ABORT, 'casebook events are append-only');
                    END;
                    CREATE TRIGGER IF NOT EXISTS events_no_delete
                    BEFORE DELETE ON events BEGIN
                        SELECT RAISE(ABORT, 'casebook events are append-only');
                    END;
                    CREATE TRIGGER IF NOT EXISTS sessions_no_update
                    BEFORE UPDATE ON sessions BEGIN
                        SELECT RAISE(ABORT, 'casebook sessions are immutable');
                    END;
                    CREATE TRIGGER IF NOT EXISTS sessions_no_delete
                    BEFORE DELETE ON sessions BEGIN
                        SELECT RAISE(ABORT, 'casebook sessions are immutable');
                    END;
                """)
                row = db.execute("SELECT schema_version FROM meta WHERE id=1").fetchone()
                if row is None:
                    if db.execute("SELECT COUNT(*) FROM events").fetchone()[0] or \
                            db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]:
                        raise IntegrityError("database records exist without a versioned head")
                    db.execute(
                        "INSERT INTO meta(id,schema_version,head_seq,head_hash) VALUES(1,?,?,?)",
                        (SCHEMA_VERSION, 0, GENESIS_HASH),
                    )
                self._verify_connection(db)
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    @staticmethod
    def _verify_connection(db: sqlite3.Connection) -> dict[str, Any]:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise IntegrityError("SQLite integrity_check failed")
        rows = db.execute("SELECT id,schema_version,head_seq,head_hash FROM meta").fetchall()
        if len(rows) != 1 or rows[0]["id"] != 1 or rows[0]["schema_version"] != SCHEMA_VERSION:
            raise IntegrityError("unexpected or missing CASEBOOK schema metadata")
        head_seq, head_hash = rows[0]["head_seq"], rows[0]["head_hash"]
        sessions = {
            row["session_id"]: dict(row)
            for row in db.execute("SELECT session_id,project,goal,created_at FROM sessions")
        }
        first_for_session: set[str] = set()
        prev_hash = GENESIS_HASH
        count = 0
        for row in db.execute("SELECT * FROM events ORDER BY seq"):
            count += 1
            if row["seq"] != count:
                raise IntegrityError("non-contiguous event sequence")
            if row["prev_hash"] != prev_hash:
                raise IntegrityError("event predecessor hash mismatch")
            session = sessions.get(row["session_id"])
            if session is None or row["project"] != session["project"]:
                raise IntegrityError("event session/project binding mismatch")
            try:
                refs = json.loads(row["evidence_refs_json"])
                meta = json.loads(row["metadata_json"])
                _validate_references(refs)
                _validate_metadata(meta)
                if (canonical_json(refs) != row["evidence_refs_json"] or
                        canonical_json(meta) != row["metadata_json"]):
                    raise ValueError("noncanonical event JSON")
                if row["type"] not in EVENT_TYPES or row["source"] not in SOURCES or \
                        row["status"] not in STATUSES:
                    raise ValueError("invalid event contract values")
                _text(row["summary"], "summary", 8192)
            except (ValueError, TypeError) as exc:
                raise IntegrityError("invalid event serialization or fields") from exc
            if row["session_id"] not in first_for_session:
                if row["type"] != "GOAL" or row["summary"] != session["goal"] or \
                        row["timestamp"] != session["created_at"]:
                    raise IntegrityError("session initial GOAL does not match the immutable session")
                first_for_session.add(row["session_id"])
            event = _payload(
                seq=row["seq"], event_id=row["event_id"], session_id=row["session_id"],
                project=row["project"], timestamp=row["timestamp"], event_type=row["type"],
                summary=row["summary"], source=row["source"], evidence_refs=refs,
                status=row["status"], metadata=meta, prev_hash=row["prev_hash"],
            )
            if _event_digest(event) != row["event_hash"]:
                raise IntegrityError("event content digest mismatch")
            prev_hash = row["event_hash"]
        if first_for_session != set(sessions):
            raise IntegrityError("session exists without an initial GOAL event")
        if head_seq != count or head_hash != prev_hash:
            raise IntegrityError("stored journal head does not match its event chain")
        return {
            "schema": "voodoo-casebook/integrity-report/v1",
            "verdict": "VERIFIED_INTEGRITY",
            "session_count": len(sessions),
            "event_count": count,
            "head_seq": count,
            "head_hash": prev_hash,
        }

    def verify(self) -> dict[str, Any]:
        """Read-only integrity verification, including SQLite physical checks and a complete hash chain."""
        with closing(self._connect(write=False)) as db:
            try:
                return self._verify_connection(db)
            except sqlite3.DatabaseError as exc:
                raise IntegrityError("SQLite data or schema cannot be verified") from exc

    @staticmethod
    def _append_transaction(
        db: sqlite3.Connection, *, session_id: str, project: str,
        event_type: str, summary: str, source: str,
        evidence_refs: list[str], metadata: dict[str, Any],
        status: str, timestamp: str, event_id: str,
    ) -> dict[str, Any]:
        head = db.execute("SELECT head_seq,head_hash FROM meta WHERE id=1").fetchone()
        seq = head["head_seq"] + 1
        event = _payload(
            seq=seq, event_id=event_id, session_id=session_id, project=project,
            timestamp=timestamp, event_type=event_type, summary=summary,
            source=source, evidence_refs=evidence_refs, metadata=metadata,
            status=status, prev_hash=head["head_hash"],
        )
        digest = _event_digest(event)
        db.execute(
            """INSERT INTO events(
                   seq,event_id,session_id,project,timestamp,type,summary,source,
                   evidence_refs_json,status,metadata_json,prev_hash,event_hash
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (seq, event_id, session_id, project, timestamp, event_type, summary, source,
             canonical_json(evidence_refs), status, canonical_json(metadata),
             head["head_hash"], digest),
        )
        db.execute(
            "UPDATE meta SET head_seq=?,head_hash=? WHERE id=1",
            (seq, digest),
        )
        return {**event, "event_hash": digest}

    def start_session(self, *, project: str, goal: str, session_id: str | None = None) -> str:
        project = _text(project, "project", 120)
        goal = _text(goal, "goal", 8192)
        session_id = _text(session_id or "ses_" + uuid.uuid4().hex, "session_id", 128)
        self.initialize()
        with closing(self._connect(write=True)) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._verify_connection(db)
                timestamp = utc_now()
                db.execute(
                    "INSERT INTO sessions(session_id,project,goal,created_at) VALUES(?,?,?,?)",
                    (session_id, project, goal, timestamp),
                )
                self._append_transaction(
                    db, session_id=session_id, project=project, event_type="GOAL",
                    summary=goal, source="user", evidence_refs=[], metadata={},
                    status="OPEN", timestamp=timestamp, event_id="cbk_" + uuid.uuid4().hex,
                )
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return session_id

    def append(self, *, session_id: str, event_type: str, summary: str,
               source: str = "user", evidence_refs: list[str] | None = None,
               metadata: dict[str, Any] | None = None, status: str = "OPEN",
               event_id: str | None = None) -> dict[str, Any]:
        session_id = _text(session_id, "session_id", 128)
        summary = _text(summary, "summary", 8192)
        if event_type not in EVENT_TYPES or source not in SOURCES or status not in STATUSES:
            raise ValueError("invalid event type, source or status")
        refs = _validate_references([] if evidence_refs is None else evidence_refs)
        meta = _validate_metadata({} if metadata is None else metadata)
        event_id = _text(event_id or "cbk_" + uuid.uuid4().hex, "event_id", 128)
        self.initialize()
        with closing(self._connect(write=True)) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._verify_connection(db)
                session = db.execute(
                    "SELECT project FROM sessions WHERE session_id=?", (session_id,),
                ).fetchone()
                if session is None:
                    raise LookupError("unknown CASEBOOK session")
                event = self._append_transaction(
                    db, session_id=session_id, project=session["project"],
                    event_type=event_type, summary=summary, source=source,
                    evidence_refs=refs, metadata=meta, status=status,
                    timestamp=utc_now(), event_id=event_id,
                )
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return event

    def get_session(self, session_id: str) -> dict[str, Any]:
        _text(session_id, "session_id", 128)
        with closing(self._connect(write=False)) as db:
            # One SQLite snapshot for verification and returned records.
            db.execute("BEGIN")
            self._verify_connection(db)
            row = db.execute(
                "SELECT session_id,project,goal,created_at FROM sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise LookupError("unknown CASEBOOK session")
            events = []
            for ev in db.execute(
                "SELECT * FROM events WHERE session_id=? ORDER BY seq", (session_id,),
            ):
                events.append({
                    "seq": ev["seq"], "event_id": ev["event_id"], "session_id": ev["session_id"],
                    "project": ev["project"], "timestamp": ev["timestamp"], "type": ev["type"],
                    "summary": ev["summary"], "source": ev["source"],
                    "evidence_refs": json.loads(ev["evidence_refs_json"]),
                    "status": ev["status"], "metadata": json.loads(ev["metadata_json"]),
                    "prev_hash": ev["prev_hash"], "event_hash": ev["event_hash"],
                })
            return {**dict(row), "events": events}
