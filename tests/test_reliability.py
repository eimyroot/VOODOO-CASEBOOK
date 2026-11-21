"""v0.2 reliability: idempotency, crash recovery, backup and path fail-closed tests."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from voodoo_casebook import CasebookStore, IntegrityError
from voodoo_casebook.backups import BACKUP_DIR, create_backup, verify_backup


class ReliabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="casebook-reliability-")
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name).resolve() / "evidence"
        self.store = CasebookStore(self.data)
        self.source = Path(__file__).resolve().parents[1]

    def start(self, session_id: str = "ses-retry-001") -> str:
        return self.store.start_session(
            project="test-reliability",
            goal="Keep journal intact after process restart",
            session_id=session_id,
        )

    def subprocess_cli(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-B", "-m", "voodoo_casebook", *args],
            cwd=str(self.source), capture_output=True, text=True, check=False, timeout=20,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(env or {})},
        )

    def test_repeat_event_id_is_idempotent_same_process(self) -> None:
        sid = self.start()
        first = self.store.append(
            session_id=sid, event_type="RESULT", summary="one outcome", source="tool",
            event_id="cbk-stable-1", metadata={"proof": "test"},
            evidence_refs=["test://receipt"],
        )
        second = self.store.append(
            session_id=sid, event_type="RESULT", summary="one outcome", source="tool",
            event_id="cbk-stable-1", metadata={"proof": "test"},
            evidence_refs=["test://receipt"],
        )
        self.assertEqual(first, second)
        self.assertEqual(self.store.verify()["event_count"], 2)

    def test_repeat_event_id_is_idempotent_across_restart(self) -> None:
        sid = self.start()
        a = self.store.append(
            session_id=sid, event_type="TODO", summary="exact retry",
            event_id="cbk-retry-restart",
        )
        b = CasebookStore(self.data).append(
            session_id=sid, event_type="TODO", summary="exact retry",
            event_id="cbk-retry-restart",
        )
        self.assertEqual((a["seq"], a["event_hash"]), (b["seq"], b["event_hash"]))
        self.assertEqual(self.store.verify()["event_count"], 2)

    def test_conflicting_replay_fails_without_new_event(self) -> None:
        sid = self.start()
        self.store.append(
            session_id=sid, event_type="RESULT", summary="original", event_id="cbk-unique",
        )
        with self.assertRaisesRegex(ValueError, "different"):
            self.store.append(
                session_id=sid, event_type="RESULT", summary="changed", event_id="cbk-unique",
            )
        self.assertEqual(self.store.verify()["event_count"], 2)

    def test_replay_cannot_switch_sessions(self) -> None:
        first = self.start("ses-a")
        second = self.start("ses-b")
        self.store.append(
            session_id=first, event_type="TODO", summary="t", event_id="cbk-shared",
        )
        with self.assertRaises(ValueError):
            self.store.append(
                session_id=second, event_type="TODO", summary="t", event_id="cbk-shared",
            )
        self.assertEqual(self.store.verify()["event_count"], 3)

    def test_repeat_session_id_has_same_goal_without_new_event(self) -> None:
        sid = self.start()
        again = self.start()
        self.assertEqual(again, sid)
        self.assertEqual(self.store.verify()["event_count"], 1)

    def test_reused_session_id_with_modified_goal_is_rejected(self) -> None:
        sid = self.start()
        with self.assertRaises(ValueError):
            self.store.start_session(
                session_id=sid, project="test-reliability", goal="different",
            )
        self.assertEqual(self.store.verify()["event_count"], 1)

    def test_explicit_data_root_required_even_when_home_changes(self) -> None:
        temp_home = Path(self.tmp.name) / "fakehome"
        temp_home.mkdir()
        result = self.subprocess_cli("init", env={
            "HOME": str(temp_home), "VOODOO_CASEBOOK_DATA_ROOT": "",
        })
        # Explicit empty config is invalid; no ephemeral HOME-derived store.
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "BLOCKED")
        self.assertFalse((temp_home / "0_EVIDENCE").exists())

    def test_explicit_env_path_is_supported(self) -> None:
        result = self.subprocess_cli("init", env={
            "VOODOO_CASEBOOK_DATA_ROOT": str(self.data),
        })
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.store.verify()["event_count"], 0)

    def test_file_permission_widening_denies_read_and_write(self) -> None:
        sid = self.start()
        db = self.data / "casebook.sqlite3"
        db.chmod(0o644)
        try:
            with self.assertRaises(PermissionError):
                self.store.verify()
            with self.assertRaises(PermissionError):
                self.store.append(
                    session_id=sid, event_type="TODO", summary="denied",
                )
        finally:
            db.chmod(0o600)

    def test_verified_backup_restorable_and_data_matches(self) -> None:
        sid = self.start()
        self.store.append(
            session_id=sid, event_type="EVIDENCE", summary="synthetic-proof",
        )
        record = create_backup(self.store)
        self.assertEqual(record["status"], "BACKUP_VERIFIED")
        self.assertEqual(record["head_seq"], 2)
        snapshot = Path(record["directory"])
        self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE((snapshot / "casebook.sqlite3").stat().st_mode), 0o600,
        )
        self.assertEqual(
            self.store.get_session(sid),
            CasebookStore(snapshot).get_session(sid),
        )
        self.assertEqual(
            verify_backup(self.store, record["backup_id"])["head_hash"],
            self.store.verify()["head_hash"],
        )

    def test_backup_is_a_point_in_time_snapshot(self) -> None:
        sid = self.start()
        result = create_backup(self.store)
        self.store.append(
            session_id=sid, event_type="TODO", summary="post-snapshot",
        )
        restored = CasebookStore(Path(result["directory"]))
        self.assertEqual(restored.verify()["event_count"], 1)
        self.assertEqual(self.store.verify()["event_count"], 2)

    def test_modified_backup_bytes_detected(self) -> None:
        self.start()
        record = create_backup(self.store)
        snapshot = Path(record["directory"])
        (snapshot / "casebook.sqlite3").write_bytes(b"forged backup")
        with self.assertRaisesRegex(IntegrityError, "digest"):
            verify_backup(self.store, record["backup_id"])

    def test_modified_backup_manifest_detected(self) -> None:
        self.start()
        record = create_backup(self.store)
        manifest_file = Path(record["directory"]) / "manifest.json"
        manifest = json.loads(manifest_file.read_text())
        manifest["head_hash"] = "f" * 64
        manifest_file.write_text(json.dumps(manifest))
        with self.assertRaises(IntegrityError):
            verify_backup(self.store, record["backup_id"])

    def test_missing_append_only_trigger_never_silently_repaired(self) -> None:
        self.start()
        with sqlite3.connect(str(self.data / "casebook.sqlite3")) as db:
            db.execute("DROP TRIGGER events_no_delete")
        with self.assertRaisesRegex(IntegrityError, "trigger"):
            self.store.verify()
        with self.assertRaisesRegex(IntegrityError, "trigger"):
            self.store.append(
                session_id="ses-retry-001", event_type="RESULT", summary="blocked",
            )
        with sqlite3.connect(str(self.data / "casebook.sqlite3")) as db:
            names = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'",
            )}
        self.assertNotIn("events_no_delete", names)

    def test_verify_concurrent_commit_reads_consistent_snapshot(self) -> None:
        # Deliberately interleave a committed WAL append between the head
        # SELECT and the event stream SELECT of one read-only verification.
        # Without BEGIN, each SELECT may observe a different snapshot.
        sid = self.start()
        from contextlib import closing

        with closing(self.store._connect(write=True)) as keeper:
            self.assertTrue((self.data / "casebook.sqlite3-wal").exists())
            original = self.store._connect
            observed = {}

            def intercepted_connect(*, write):
                db = original(write=write)
                if not write:
                    def trace(sql):
                        if "SELECT * FROM events ORDER BY seq" not in sql or observed.get("entered"):
                            return
                        observed["entered"] = True

                        def write_event():
                            try:
                                observed["event"] = CasebookStore(self.data).append(
                                    session_id=sid, event_type="TODO",
                                    summary="committed during verification",
                                    event_id="cbk_concurrent_verification",
                                )
                            except Exception as exc:
                                observed["error"] = repr(exc)

                        thread = threading.Thread(target=write_event)
                        thread.start()
                        thread.join(timeout=10)
                        observed["joined"] = not thread.is_alive()

                    db.set_trace_callback(trace)
                return db

            self.store._connect = intercepted_connect
            try:
                snapshot = self.store.verify()
            finally:
                self.store._connect = original

        self.assertTrue(observed.get("entered"), observed)
        self.assertTrue(observed.get("joined"), observed)
        self.assertNotIn("error", observed)
        self.assertEqual(snapshot["event_count"], 1)
        self.assertEqual(CasebookStore(self.data).verify()["event_count"], 2)

    def test_backup_id_traversal_fails_closed(self) -> None:
        self.start()
        for value in ("../escape", "/tmp", "", "x" * 32, "bad\\name"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                verify_backup(self.store, value)

    def test_backup_root_symlink_fails_closed(self) -> None:
        self.start()
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir(mode=0o700)
        (self.data / BACKUP_DIR).symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            create_backup(self.store)
        self.assertEqual(self.store.verify()["event_count"], 1)

    def test_crash_before_commit_rolls_back_and_retry_writes_once(self) -> None:
        sid = self.start()
        script = (
            "import os; from pathlib import Path; "
            "from voodoo_casebook import CasebookStore; "
            "s=CasebookStore(Path(" + repr(str(self.data)) + ")); "
            "orig=s._append_transaction; "
            "deflater=None\n"
            "def crash(*args, **kwargs):\n"
            "  orig(*args, **kwargs)\n"
            "  os._exit(71)\n"
            "s._append_transaction=crash\n"
            "s.append(session_id=" + repr(sid) + ",event_type='RESULT',"
            "summary='commit-retry',event_id='cbk-crash-rollback')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-B", "-c", script], cwd=str(self.source),
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(proc.returncode, 71, proc.stderr)
        self.assertEqual(CasebookStore(self.data).verify()["event_count"], 1)
        self.store.append(
            session_id=sid, event_type="RESULT", summary="commit-retry",
            event_id="cbk-crash-rollback",
        )
        self.assertEqual(self.store.verify()["event_count"], 2)

    def test_crash_after_commit_response_lost_replay_is_idempotent(self) -> None:
        sid = self.start()
        script = (
            "import os; from pathlib import Path; "
            "from voodoo_casebook import CasebookStore; "
            "s=CasebookStore(Path(" + repr(str(self.data)) + ")); "
            "s.append(session_id=" + repr(sid) + ",event_type='RESULT',"
            "summary='committed',event_id='cbk-crash-after'); os._exit(72)"
        )
        proc = subprocess.run(
            [sys.executable, "-B", "-c", script], cwd=str(self.source),
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(proc.returncode, 72, proc.stderr)
        result = CasebookStore(self.data).append(
            session_id=sid, event_type="RESULT", summary="committed",
            event_id="cbk-crash-after",
        )
        self.assertEqual(result["seq"], 2)
        self.assertEqual(self.store.verify()["event_count"], 2)

    def test_cli_backup_creation_and_separate_verification(self) -> None:
        self.start()
        a = self.subprocess_cli(
            "--data-root", str(self.data), "backup",
        )
        self.assertEqual(a.returncode, 0, a.stdout + a.stderr)
        record = json.loads(a.stdout)
        b = self.subprocess_cli(
            "--data-root", str(self.data), "backup-verify",
            "--backup-id", record["backup_id"],
        )
        self.assertEqual(b.returncode, 0, b.stdout + b.stderr)
        self.assertEqual(json.loads(b.stdout)["status"], "BACKUP_VERIFIED")


if __name__ == "__main__":
    unittest.main()
