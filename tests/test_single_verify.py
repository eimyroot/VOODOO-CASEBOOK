"""v0.4 single-pass existing-store writes, without weakening full-chain validation."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voodoo_casebook import CasebookStore, IntegrityError


class SingleVerifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="casebook-one-pass-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "evidence"
        self.store = CasebookStore(self.root)
        self.sid = self.store.start_session(
            project="test-v04", goal="one full audit per existing-store write",
            session_id="session-v04",
        )

    def test_append_existing_store_scans_exactly_once(self):
        with patch.object(
            CasebookStore, "_verify_connection",
            wraps=CasebookStore._verify_connection,
        ) as verify:
            event = self.store.append(
                session_id=self.sid, event_type="TODO", summary="append once",
                event_id="v04-append-one",
            )
        self.assertEqual(verify.call_count, 1, "existing store must not be verified twice")
        self.assertEqual(event["seq"], 2)
        self.assertEqual(self.store.verify()["event_count"], 2)

    def test_start_existing_store_scans_exactly_once(self):
        with patch.object(
            CasebookStore, "_verify_connection",
            wraps=CasebookStore._verify_connection,
        ) as verify:
            sid = self.store.start_session(
                project="test-v04", goal="second goal", session_id="session-v04-2",
            )
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(self.store.get_session(sid)["events"][0]["type"], "GOAL")

    def test_duplicate_retry_scans_exactly_once(self):
        self.store.append(
            session_id=self.sid, event_type="TODO", summary="idempotent",
            event_id="v04-replay",
        )
        with patch.object(
            CasebookStore, "_verify_connection",
            wraps=CasebookStore._verify_connection,
        ) as verify:
            again = self.store.append(
                session_id=self.sid, event_type="TODO", summary="idempotent",
                event_id="v04-replay",
            )
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(again["seq"], 2)
        self.assertEqual(self.store.verify()["event_count"], 2)

    def test_missing_store_bootstraps_without_trusting_unknown_session(self):
        fresh = CasebookStore(Path(self.tmp.name).resolve() / "another")
        with self.assertRaises(LookupError):
            fresh.append(
                session_id="nonexistent", event_type="TODO",
                summary="not an implicit session", event_id="v04-unknown",
            )
        self.assertEqual(fresh.verify()["event_count"], 0)

    def test_historical_event_tamper_still_blocks_write(self):
        self.store.append(
            session_id=self.sid, event_type="TODO", summary="past legitimate",
            event_id="v04-before-tamper",
        )
        with sqlite3.connect(str(self.store.database)) as db:
            db.executescript("""
                DROP TRIGGER events_no_update;
                UPDATE events SET summary='forged old event' WHERE seq=2;
                CREATE TRIGGER events_no_update
                BEFORE UPDATE ON events BEGIN
                    SELECT RAISE(ABORT,'casebook events are append-only');
                END;
            """)
        with self.assertRaisesRegex(IntegrityError, "digest"):
            self.store.append(
                session_id=self.sid, event_type="TODO", summary="must refuse",
                event_id="v04-blocked",
            )
        with sqlite3.connect(str(self.store.database)) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2,
            )

    def test_missing_mandatory_trigger_still_blocks_write(self):
        with sqlite3.connect(str(self.store.database)) as db:
            db.execute("DROP TRIGGER events_no_delete")
        with self.assertRaisesRegex(IntegrityError, "trigger"):
            self.store.append(
                session_id=self.sid, event_type="TODO", summary="refused",
                event_id="v04-no-trigger",
            )
        with sqlite3.connect(str(self.store.database)) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1,
            )

    def test_explicit_initialize_still_verifies_entire_history(self):
        self.store.append(
            session_id=self.sid, event_type="TODO", summary="legit",
            event_id="v04-init-historical",
        )
        with sqlite3.connect(str(self.store.database)) as db:
            db.execute("UPDATE meta SET head_hash=?", ("f" * 64,))
        with self.assertRaisesRegex(IntegrityError, "head"):
            self.store.initialize()
        with self.assertRaises(IntegrityError):
            self.store.append(
                session_id=self.sid, event_type="TODO", summary="refused",
                event_id="v04-init-refused",
            )


if __name__ == "__main__":
    unittest.main()
