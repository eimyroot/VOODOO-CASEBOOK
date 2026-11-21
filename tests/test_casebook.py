"""Independent behavioral/negative contract suite for CASEBOOK v0.1 (stdlib only)."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from voodoo_casebook import CasebookStore, IntegrityError
from voodoo_casebook.core import canonical_json, DATABASE_FILENAME, GENESIS_HASH


class CasebookContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="voodoo-casebook-test-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "evidence"
        self.store = CasebookStore(self.root)

    def _created(self, project: str = "test-project") -> str:
        return self.store.start_session(
            project=project, goal="Test persistence without secrets",
        )

    def _sql(self, query: str, params: tuple = ()) -> None:
        with sqlite3.connect(str(self.root / DATABASE_FILENAME)) as db:
            db.execute(query, params)

    def test_initialize_empty_and_verify(self) -> None:
        self.store.initialize()
        report = self.store.verify()
        self.assertEqual(report["verdict"], "VERIFIED_INTEGRITY")
        self.assertEqual(report["event_count"], 0)
        self.assertEqual(report["head_hash"], GENESIS_HASH)
        self.assertEqual((self.root / DATABASE_FILENAME).stat().st_mode & 0o077, 0)

    def test_restart_reopens_same_bytes_and_attribution(self) -> None:
        sid = self._created()
        ev = self.store.append(
            session_id=sid, event_type="DECISION",
            summary="Choose SQLite", source="assistant",
            evidence_refs=["git://example/commit#one"],
            metadata={"verified": False},
        )
        restarted = CasebookStore(self.root)
        session = restarted.get_session(sid)
        self.assertEqual(session["events"][0]["type"], "GOAL")
        self.assertEqual(session["events"][1]["event_id"], ev["event_id"])
        self.assertEqual(session["events"][1]["evidence_refs"], ["git://example/commit#one"])
        self.assertFalse(session["events"][1]["metadata"]["verified"])
        self.assertEqual(restarted.verify()["event_count"], 2)

    def test_hash_chain_independent_calculation(self) -> None:
        sid = self._created()
        ev = self.store.append(session_id=sid, event_type="RESULT", summary="Observed test outcome")
        report = self.store.verify()
        self.assertEqual(ev["prev_hash"], self.store.get_session(sid)["events"][0]["event_hash"])
        expected = hashlib.sha256(canonical_json({
            key: value for key, value in ev.items() if key != "event_hash"
        }).encode("utf-8")).hexdigest()
        self.assertEqual(ev["event_hash"], expected)
        self.assertEqual(report["head_hash"], expected)

    def test_append_only_triggers(self) -> None:
        self._created()
        with self.assertRaises(sqlite3.IntegrityError):
            self._sql("UPDATE events SET summary='forged' WHERE seq=1")
        with self.assertRaises(sqlite3.IntegrityError):
            self._sql("DELETE FROM events WHERE seq=1")
        with self.assertRaises(sqlite3.IntegrityError):
            self._sql("DELETE FROM sessions")
        self.assertEqual(self.store.verify()["event_count"], 1)

    def test_event_content_tamper_detected(self) -> None:
        self._created()
        self._sql("DROP TRIGGER events_no_update")
        self._sql("UPDATE events SET summary='forged' WHERE seq=1")
        with self.assertRaisesRegex(IntegrityError, "trigger|digest|GOAL"):
            self.store.verify()
        with self.assertRaises(IntegrityError):
            self.store.append(session_id="unused", event_type="TODO", summary="blocked")

    def test_head_tamper_detected(self) -> None:
        self._created()
        self._sql("UPDATE meta SET head_hash=?", ("f" * 64,))
        with self.assertRaisesRegex(IntegrityError, "head"):
            self.store.verify()

    def test_event_delete_detected_even_with_trigger_removed(self) -> None:
        self._created()
        self._sql("DROP TRIGGER events_no_delete")
        self._sql("DELETE FROM events WHERE seq=1")
        with self.assertRaises(IntegrityError):
            self.store.verify()

    def test_session_tamper_detected(self) -> None:
        sid = self._created()
        self._sql("DROP TRIGGER sessions_no_update")
        self._sql("UPDATE sessions SET project=? WHERE session_id=?", ("other", sid))
        with self.assertRaises(IntegrityError):
            self.store.verify()

    def test_out_of_order_event_detected(self) -> None:
        sid = self._created()
        self.store.append(session_id=sid, event_type="TODO", summary="item")
        self._sql("DROP TRIGGER events_no_update")
        self._sql("UPDATE events SET seq=7 WHERE seq=2")
        with self.assertRaisesRegex(IntegrityError, "trigger|sequence"):
            self.store.verify()

    def test_duplicate_event_id_is_rejected_without_mutation(self) -> None:
        sid = self._created()
        self.store.append(session_id=sid, event_type="TODO", summary="A", event_id="cbk_same")
        with self.assertRaises(ValueError):
            self.store.append(
                session_id=sid, event_type="TODO", summary="B", event_id="cbk_same",
            )
        self.assertEqual(self.store.verify()["event_count"], 2)

    def test_duplicate_session_is_atomic(self) -> None:
        sid = self._created()
        with self.assertRaises(ValueError):
            self.store.start_session(
                project="other", goal="must not overwrite", session_id=sid,
            )
        self.assertEqual(self.store.verify()["event_count"], 1)
        self.assertEqual(self.store.get_session(sid)["project"], "test-project")

    def test_schema_version_tamper_is_blocked(self) -> None:
        self._created()
        self._sql("UPDATE meta SET schema_version=999 WHERE id=1")
        with self.assertRaises(IntegrityError):
            self.store.verify()

    def test_missing_session_rejected(self) -> None:
        self.store.initialize()
        with self.assertRaises(LookupError):
            self.store.append(session_id="unknown", event_type="TODO", summary="bad")
        self.assertEqual(self.store.verify()["event_count"], 0)

    def test_invalid_event_types_sources_and_metadata_rejected(self) -> None:
        sid = self._created()
        bad = [
            {"event_type": "EXECUTE", "summary": "bad"},
            {"event_type": "TODO", "summary": "bad", "source": "admin"},
            {"event_type": "TODO", "summary": "bad", "status": "VERIFIED"},
            {"event_type": "TODO", "summary": ""},
            {"event_type": "TODO", "summary": "bad", "metadata": {"nan": float("nan")}},
            {"event_type": "TODO", "summary": "bad", "evidence_refs": ["x"] * 33},
        ]
        for item in bad:
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.store.append(session_id=sid, **item)
        self.assertEqual(self.store.verify()["event_count"], 1)

    def test_projects_and_sessions_are_isolated(self) -> None:
        a = self._created("project-A")
        b = self._created("project-B")
        self.store.append(session_id=a, event_type="TODO", summary="Only A")
        self.store.append(session_id=b, event_type="DECISION", summary="Only B")
        self.assertEqual(self.store.get_session(a)["project"], "project-A")
        self.assertEqual(self.store.get_session(b)["project"], "project-B")
        self.assertEqual(
            [e["summary"] for e in self.store.get_session(b)["events"]],
            ["Test persistence without secrets", "Only B"],
        )
        self.assertEqual(self.store.verify()["event_count"], 4)

    def test_prevent_source_repository_data_path(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        store = CasebookStore(project_root / "private")
        with self.assertRaisesRegex(ValueError, "outside"):
            store.initialize()
        self.assertFalse((project_root / "private").exists())

    def test_symlink_data_root_fails_closed(self) -> None:
        self.root.mkdir(mode=0o700)
        alias = self.base / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            CasebookStore(alias).initialize()

    def test_world_readable_root_refused(self) -> None:
        self.root.mkdir(mode=0o755)
        self.root.chmod(0o755)
        with self.assertRaises(PermissionError):
            self.store.initialize()

    def test_traversal_relative_root_refused(self) -> None:
        with self.assertRaises(ValueError):
            CasebookStore(Path("../unsafe"))
        with self.assertRaises(ValueError):
            CasebookStore(self.base / ".." / "unsafe")

    def test_corrupt_database_bytes_fail_closed(self) -> None:
        self.root.mkdir(mode=0o700)
        (self.root / DATABASE_FILENAME).write_bytes(b"Not a SQLite database")
        (self.root / DATABASE_FILENAME).chmod(0o600)
        with self.assertRaises((IntegrityError, sqlite3.DatabaseError)):
            self.store.verify()

    def test_backup_restore_into_new_private_root(self) -> None:
        sid = self._created()
        self.store.append(session_id=sid, event_type="EVIDENCE", summary="Proof pointer")
        target = self.base / "restore"
        target.mkdir(mode=0o700)
        with closing(sqlite3.connect(str(self.root / DATABASE_FILENAME))) as src:
            with closing(sqlite3.connect(str(target / DATABASE_FILENAME))) as dst:
                src.backup(dst)
                dst.commit()
        (target / DATABASE_FILENAME).chmod(0o600)
        restored = CasebookStore(target)
        self.assertEqual(restored.verify(), self.store.verify())
        self.assertEqual(restored.get_session(sid), self.store.get_session(sid))

    def test_concurrent_append_keeps_one_ordered_chain(self) -> None:
        sid = self._created()
        errors: list[str] = []
        def run(i: int) -> None:
            try:
                CasebookStore(self.root).append(
                    session_id=sid, event_type="TODO", summary="item-" + str(i),
                )
            except Exception as exc:
                errors.append(repr(exc))
        threads = [threading.Thread(target=run, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        report = self.store.verify()
        self.assertEqual(report["event_count"], 9)
        self.assertEqual(
            sorted(ev["summary"] for ev in self.store.get_session(sid)["events"][1:]),
            ["item-" + str(i) for i in range(8)],
        )

    def test_cli_independent_process_restart(self) -> None:
        root = self.base / "cli-data"
        cwd = Path(__file__).resolve().parents[1]
        def invoke(*args: str) -> dict:
            process = subprocess.run(
                [sys.executable, "-B", "-m", "voodoo_casebook",
                 "--data-root", str(root)] + list(args),
                cwd=str(cwd), capture_output=True, text=True, check=False,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                timeout=15,
            )
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            return json.loads(process.stdout)
        started = invoke("start", "--project", "example", "--goal", "restart check")
        sid = started["session_id"]
        invoke("append", "--session", sid, "--type", "RESULT", "--summary", "durable")
        fresh_process = invoke("show", "--session", sid)
        self.assertEqual(fresh_process["events"][-1]["summary"], "durable")
        self.assertEqual(invoke("verify")["event_count"], 2)

    def test_cli_missing_database_does_not_create_data(self) -> None:
        root = self.base / "not-created"
        result = subprocess.run(
            [sys.executable, "-B", "-m", "voodoo_casebook",
             "--data-root", str(root), "verify"],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(root.exists())
        self.assertEqual(json.loads(result.stdout)["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
