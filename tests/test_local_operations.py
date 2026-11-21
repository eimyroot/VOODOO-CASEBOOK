"""v0.6 local-only operations: bounded cold-start concurrency and isolated restore."""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from voodoo_casebook import CasebookStore, IntegrityError
from voodoo_casebook.backups import create_backup, restore_backup_to_new_root


class LocalOperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cbk-v06-local-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "live"
        self.store = CasebookStore(self.root)
        self.source = Path(__file__).resolve().parents[1]

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, "-B", "-m", "voodoo_casebook", *argv],
            cwd=str(self.source),
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True, text=True, timeout=15,
        )

    def _parallel_first_sessions(self, ids):
        gate = self.base / "go"
        processes = []
        runner = """import json,sys,time
from pathlib import Path
from voodoo_casebook import CasebookStore
root=Path(sys.argv[1]); gate=Path(sys.argv[2]); sid=sys.argv[3]
Path(sys.argv[4]).write_text('ready')
while not gate.exists(): time.sleep(.005)
try:
  result=CasebookStore(root).start_session(project='synthetic',goal='parallel start',session_id=sid)
  print(json.dumps({'status':'PASS','session_id':result}))
except Exception as e:
  print(json.dumps({'status':'ERROR','type':type(e).__name__,'reason':str(e)}))
  sys.exit(2)
"""
        for i, sid in enumerate(ids):
            ready = self.base / ("ready-" + str(i))
            p = subprocess.Popen(
                [sys.executable, "-B", "-c", runner,
                 str(self.root), str(gate), sid, str(ready)],
                cwd=str(self.source),
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            processes.append(p)
        try:
            deadline = time.monotonic() + 8
            while (len(list(self.base.glob("ready-*"))) != len(ids)
                   and time.monotonic() < deadline):
                time.sleep(0.02)
            self.assertEqual(len(list(self.base.glob("ready-*"))), len(ids))
            gate.write_text("go")
            results = []
            for p in processes:
                stdout, stderr = p.communicate(timeout=12)
                results.append((p.returncode, json.loads(stdout), stderr[-200:]))
            return results
        finally:
            for p in processes:
                if p.poll() is None:
                    p.kill()
                    p.wait(timeout=5)

    def test_multiple_processes_simultaneous_initialization(self):
        results = self._parallel_first_sessions(["sid-"+str(i) for i in range(4)])
        self.assertTrue(all(code == 0 for code, _, _ in results), results)
        self.assertEqual(self.store.verify()["event_count"], 4)
        self.assertEqual(self.store.verify()["session_count"], 4)
        self.assertEqual(stat.S_IMODE(self.store.database.stat().st_mode), 0o600)
        lock = self.root / ".casebook-init.lock"
        self.assertTrue(lock.is_file())
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)

    def test_simultaneous_identical_first_session_idempotent(self):
        results = self._parallel_first_sessions(["same-session"] * 4)
        self.assertTrue(all(code == 0 for code, _, _ in results), results)
        self.assertEqual(self.store.verify()["event_count"], 1)
        self.assertEqual(self.store.verify()["session_count"], 1)

    def test_threaded_initialization_no_partial_schema(self):
        def initialize(i):
            return CasebookStore(self.root).start_session(
                project="synthetic", goal="threads", session_id="thread-%d" % i,
            )
        with ThreadPoolExecutor(max_workers=5) as pool:
            ids = list(pool.map(initialize, range(5)))
        self.assertEqual(len(set(ids)), 5)
        self.assertEqual(self.store.verify()["event_count"], 5)

    def test_database_secure_mode_before_sqlite_open(self):
        original = CasebookStore._connect
        checked = []
        def guard_before_connect(obj, *, write):
            if write and obj.database.exists():
                checked.append(stat.S_IMODE(obj.database.stat().st_mode))
            return original(obj, write=write)
        with patch.object(CasebookStore, "_connect", guard_before_connect):
            self.store.initialize()
        self.assertTrue(checked)
        self.assertTrue(all(x == 0o600 for x in checked))

    def test_symlink_initialization_lock_is_rejected(self):
        self.root.mkdir(mode=0o700)
        outside = self.base / "outside"
        outside.write_text("untouched")
        (self.root / ".casebook-init.lock").symlink_to(outside)
        with self.assertRaises(OSError):
            self.store.initialize()
        self.assertEqual(outside.read_text(), "untouched")

    def test_widened_lock_permissions_are_rejected(self):
        self.store.initialize()
        lock = self.root / ".casebook-init.lock"
        lock.chmod(0o644)
        try:
            with self.assertRaises(PermissionError):
                self.store.start_session(project="synthetic",goal="blocked")
        finally:
            lock.chmod(0o600)
        self.assertEqual(self.store.verify()["event_count"], 0)

    def test_restore_snapshot_into_fresh_private_root(self):
        sid = self.store.start_session(
            project="synthetic", goal="restore", session_id="restore-1",
        )
        self.store.append(session_id=sid, event_type="TODO",
                          summary="snapshot",event_id="restore-old")
        snapshot = create_backup(self.store)
        self.store.append(session_id=sid, event_type="RESULT",
                          summary="newer than snapshot",event_id="restore-new")
        destination = self.base / "recovered"
        result = restore_backup_to_new_root(
            self.store,snapshot["backup_id"],destination,
        )
        self.assertEqual(result["status"],"RESTORE_VERIFIED_ISOLATED")
        self.assertFalse(result["promoted_to_active"])
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode),0o700)
        self.assertEqual(stat.S_IMODE((destination / "casebook.sqlite3").stat().st_mode),0o600)
        self.assertEqual(CasebookStore(destination).verify()["event_count"],2)
        self.assertEqual(self.store.verify()["event_count"],3)
        self.assertEqual(
            CasebookStore(destination).get_session(sid)["events"][1]["event_id"],"restore-old",
        )

    def test_existing_destination_never_overwritten(self):
        self.store.start_session(project="synthetic",goal="do not replace")
        snap=create_backup(self.store)
        target=self.base/"existing"
        target.mkdir(mode=0o700)
        (target/"marker").write_text("keep")
        with self.assertRaises(FileExistsError):
            restore_backup_to_new_root(self.store,snap["backup_id"],target)
        self.assertEqual((target/"marker").read_text(),"keep")
        self.assertFalse((target/"casebook.sqlite3").exists())

    def test_restore_cannot_target_active_data_root_or_descendant(self):
        self.store.start_session(project="synthetic",goal="keep live")
        snap=create_backup(self.store)
        for target in [self.root,self.root/"somewhere","relative/path"]:
            with self.subTest(target=str(target)):
                with self.assertRaises((ValueError,FileExistsError)):
                    restore_backup_to_new_root(self.store,snap["backup_id"],Path(target))
        self.assertEqual(self.store.verify()["event_count"],1)

    def test_tampered_backup_blocks_restore_without_target(self):
        self.store.start_session(project="synthetic",goal="tamper")
        snap=create_backup(self.store)
        snapshot=Path(snap["directory"])/"casebook.sqlite3"
        snapshot.write_bytes(b"tampered copy")
        destination=self.base/"should-not-exist"
        with self.assertRaises(IntegrityError):
            restore_backup_to_new_root(self.store,snap["backup_id"],destination)
        self.assertFalse(destination.exists())

    def test_broad_parent_permissions_block_restore(self):
        self.store.start_session(project="synthetic",goal="unsafe parent")
        snap=create_backup(self.store)
        parent=self.base/"public-ish"
        parent.mkdir(mode=0o755)
        with self.assertRaises(PermissionError):
            restore_backup_to_new_root(self.store,snap["backup_id"],parent/"restore")
        self.assertFalse((parent/"restore").exists())

    def test_restore_failed_copy_cleans_only_new_target(self):
        self.store.start_session(project="synthetic",goal="partial failure")
        snap=create_backup(self.store)
        target=self.base/"partial"
        with patch("voodoo_casebook.backups.shutil.copyfileobj",
                   side_effect=OSError("synthetic copy failure")):
            with self.assertRaisesRegex(OSError,"synthetic copy failure"):
                restore_backup_to_new_root(self.store,snap["backup_id"],target)
        self.assertFalse(target.exists())
        self.assertEqual(self.store.verify()["event_count"],1)

    def test_cli_restore_to_new_root_is_explicit_and_nonactivating(self):
        sid=self.store.start_session(project="synthetic",goal="cli restore",
                                     session_id="cli-recovery")
        self.store.append(session_id=sid,event_type="TODO",summary="saved",
                          event_id="cli-old")
        snap=create_backup(self.store)
        target=self.base/"cli-restored"
        p=self.run_cli("--data-root",str(self.root),"restore-to-new-root",
                       "--backup-id",snap["backup_id"],"--to",str(target))
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        receipt=json.loads(p.stdout)
        self.assertEqual(receipt["status"],"RESTORE_VERIFIED_ISOLATED")
        self.assertEqual(receipt["event_count"],2)
        self.assertFalse(receipt["promoted_to_active"])
        self.assertEqual(self.store.verify()["event_count"],2)
        again=self.run_cli("--data-root",str(self.root),"restore-to-new-root",
                           "--backup-id",snap["backup_id"],"--to",str(target))
        self.assertEqual(again.returncode,2)
        self.assertEqual(json.loads(again.stdout)["status"],"BLOCKED")

    def test_restore_symlink_destination_is_rejected(self):
        self.store.start_session(project="synthetic",goal="symlink")
        snap=create_backup(self.store)
        target=self.base/"link-target"
        target.symlink_to(self.base / "notthere",target_is_directory=True)
        with self.assertRaises(FileExistsError):
            restore_backup_to_new_root(self.store,snap["backup_id"],target)
        self.assertTrue(target.is_symlink())


    def test_interrupted_initialization_fails_closed(self):
        with patch.object(CasebookStore, "_connect",
                          side_effect=OSError("synthetic interrupted bootstrap")):
            with self.assertRaisesRegex(OSError,"interrupted bootstrap"):
                self.store.initialize()
        self.assertTrue(self.store.database.is_file())
        self.assertEqual(stat.S_IMODE(self.store.database.stat().st_mode),0o600)
        with self.assertRaises((IntegrityError,sqlite3.DatabaseError)):
            self.store.initialize()
        self.assertTrue(self.store.database.is_file())

    def test_restore_to_development_source_tree_is_rejected(self):
        self.store.start_session(project="synthetic",goal="source hygiene")
        snap=create_backup(self.store)
        destination=Path("/Users/eimyna/0_DEV/VOODOO-CASEBOOK/not-to-be-written")
        with self.assertRaisesRegex(ValueError,"development source tree"):
            restore_backup_to_new_root(self.store,snap["backup_id"],destination)
        self.assertFalse(destination.exists())

    def test_warm_existing_database_keeps_one_full_audit(self):
        self.store.start_session(project="synthetic",goal="warm")
        with patch.object(CasebookStore, "_verify_connection",
                          wraps=CasebookStore._verify_connection) as verify:
            with self.assertRaises(LookupError):
                CasebookStore(self.root).append(
                    session_id="ses-warm-existing-does-not-exist",
                    event_type="TODO",summary="refuse",event_id="cbk-warm-refuse",
                )
        self.assertEqual(verify.call_count,1)
        self.assertEqual(self.store.verify()["event_count"],1)


if __name__=="__main__":
    unittest.main()
