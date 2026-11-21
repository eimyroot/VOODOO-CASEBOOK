"""v0.5: bounded, single-transaction batch throughput and fail-closed security."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from voodoo_casebook import CasebookStore, IntegrityError
from voodoo_casebook.backups import create_backup, verify_backup


class AtomicBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cbk-atomic-batch-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "private"
        self.store = CasebookStore(self.root)
        self.sid = self.store.start_session(
            project="synthetic", goal="Batch integrity contract",
            session_id="batch-session-01",
        )

    @staticmethod
    def batch(*ids):
        return [
            {
                "event_id": event_id,
                "type": "TODO",
                "summary": "synthetic " + event_id,
            }
            for event_id in ids
        ]

    def test_batch_one_full_integrity_audit_for_12_events(self):
        ids = ["ab-" + str(n) for n in range(12)]
        with patch.object(
            CasebookStore, "_verify_connection",
            wraps=CasebookStore._verify_connection,
        ) as verify:
            results = self.store.append_batch(
                session_id=self.sid, events=self.batch(*ids),
            )
        self.assertEqual(verify.call_count, 1)
        self.assertEqual([item["seq"] for item in results], list(range(2, 14)))
        self.assertEqual([item["event_id"] for item in results], ids)
        self.assertEqual(self.store.verify()["event_count"], 13)

    def test_batch_one_event_matches_existing_append_contract(self):
        event = self.store.append_batch(session_id=self.sid, events=[{
            "type": "DECISION", "summary": "unicode: čž✓", "source": "assistant",
            "event_id": "ab-unicode", "metadata": {"x": ["a", 1]},
            "evidence_refs": ["https://example.com/a"], "status": "CANDIDATE",
        }])[0]
        self.assertEqual(event["source"], "assistant")
        self.assertEqual(event["status"], "CANDIDATE")
        self.assertEqual(event["metadata"], {"x": ["a", 1]})
        self.assertEqual(self.store.get_session(self.sid)["events"][1], {
            key: value for key, value in event.items() if key != "schema"
        })

    def test_exact_batch_retry_does_not_duplicate_records(self):
        req = self.batch("ab-retry-one", "ab-retry-two")
        original = self.store.append_batch(session_id=self.sid, events=req)
        replay = self.store.append_batch(session_id=self.sid, events=req)
        self.assertEqual(replay, original)
        self.assertEqual(self.store.verify()["event_count"], 3)

    def test_mixed_replay_and_new_event_is_consistent(self):
        original = self.store.append_batch(
            session_id=self.sid, events=self.batch("ab-old"),
        )
        combined = self.store.append_batch(
            session_id=self.sid, events=self.batch("ab-old", "ab-new"),
        )
        self.assertEqual(combined[0], original[0])
        self.assertEqual(combined[1]["seq"], 3)
        self.assertEqual(self.store.verify()["event_count"], 3)

    def test_conflict_on_later_replay_rolls_back_new_event(self):
        self.store.append(session_id=self.sid, event_type="TODO",
                          summary="original", event_id="ab-existing")
        before = self.store.verify()
        request = self.batch("ab-new-before-conflict") + [
            {"event_id": "ab-existing", "type": "TODO", "summary": "conflict"},
        ]
        with self.assertRaisesRegex(ValueError, "event_id reused"):
            self.store.append_batch(session_id=self.sid, events=request)
        after = self.store.verify()
        self.assertEqual(after["head_hash"], before["head_hash"])
        self.assertEqual(after["event_count"], before["event_count"])

    def test_exception_during_second_insert_rolls_back_first(self):
        original = self.store._append_transaction
        calls = []
        def crash_on_second(*args, **kwargs):
            calls.append(kwargs["event_id"])
            if len(calls) == 2:
                raise RuntimeError("synthetic interruption")
            return original(*args, **kwargs)
        with patch.object(self.store, "_append_transaction", crash_on_second):
            with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
                self.store.append_batch(
                    session_id=self.sid,
                    events=self.batch("ab-first", "ab-second"),
                )
        self.assertEqual(self.store.verify()["event_count"], 1)

    def test_historical_event_tampering_blocks_entire_batch(self):
        self.store.append(session_id=self.sid, event_type="TODO",
                          summary="previous", event_id="ab-history")
        with sqlite3.connect(str(self.store.database)) as db:
            db.executescript("""
                DROP TRIGGER events_no_update;
                UPDATE events SET summary='forged' WHERE seq=2;
                CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN
                    SELECT RAISE(ABORT, 'casebook events are append-only');
                END;
            """)
        with self.assertRaises(IntegrityError):
            self.store.append_batch(
                session_id=self.sid, events=self.batch("ab-blocked"),
            )
        with sqlite3.connect(str(self.store.database)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)

    def test_missing_trigger_blocks_entire_batch(self):
        with sqlite3.connect(str(self.store.database)) as db:
            db.execute("DROP TRIGGER events_no_delete")
        with self.assertRaisesRegex(IntegrityError, "trigger"):
            self.store.append_batch(
                session_id=self.sid, events=self.batch("ab-blocked"),
            )

    def test_invalid_requests_fail_before_db_mutation(self):
        inputs = [
            [],
            self.batch(*["ab-" + str(i) for i in range(33)]),
            [{"type": "TODO"}],
            [{"type": "TODO", "summary": "s", "unknown": True}],
            self.batch("ab-duplicate", "ab-duplicate"),
            [{"type": "INVALID", "summary": "s"}],
            [{"type": [], "summary": "s"}],
            [{"type": "TODO", "summary": "s", "metadata": set()}],
        ]
        before = self.store.verify()
        for request in inputs:
            with self.subTest(request=str(request)[:80]):
                with self.assertRaises((TypeError, ValueError)):
                    self.store.append_batch(session_id=self.sid, events=request)
        after = self.store.verify()
        self.assertEqual(after["head_hash"], before["head_hash"])
        self.assertEqual(after["event_count"], before["event_count"])

    def test_batch_recovery_via_existing_verified_snapshot(self):
        self.store.append_batch(
            session_id=self.sid, events=self.batch("ab-save1", "ab-save2"),
        )
        backed = create_backup(self.store)
        restored = CasebookStore(Path(backed["directory"]))
        self.assertEqual(
            self.store.get_session(self.sid), restored.get_session(self.sid),
        )
        self.assertEqual(
            verify_backup(self.store, backed["backup_id"])["status"],
            "BACKUP_VERIFIED",
        )

    def test_parallel_batches_preserve_one_global_chain(self):
        def write_batch(group):
            return CasebookStore(self.root).append_batch(
                session_id=self.sid,
                events=self.batch(*[
                    "ab-parallel-{}-{}".format(group, n) for n in range(6)
                ]),
            )
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(write_batch, range(3)))
        self.assertEqual(sum(map(len, results)), 18)
        report = self.store.verify()
        self.assertEqual(report["event_count"], 19)
        seqs = sorted(row["seq"] for batch in results for row in batch)
        self.assertEqual(seqs, list(range(2, 20)))


    def test_maximum_32_events_per_batch_succeeds(self):
        results = self.store.append_batch(
            session_id=self.sid,
            events=self.batch(*["ab-max-" + str(i) for i in range(32)]),
        )
        self.assertEqual(len(results), 32)
        self.assertEqual(self.store.verify()["event_count"], 33)

    def test_batch_replay_cannot_cross_session_boundaries(self):
        other = self.store.start_session(
            project="synthetic", goal="other synthetic session",
            session_id="batch-session-02",
        )
        self.store.append_batch(
            session_id=other, events=self.batch("ab-other-session"),
        )
        before = self.store.verify()
        with self.assertRaisesRegex(ValueError, "event_id reused"):
            self.store.append_batch(
                session_id=self.sid,
                events=self.batch("ab-new", "ab-other-session"),
            )
        after = self.store.verify()
        self.assertEqual(after["head_hash"], before["head_hash"])
        self.assertEqual(after["event_count"], before["event_count"])


if __name__ == "__main__":
    unittest.main()
