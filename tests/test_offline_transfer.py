"""v0.7 private, disposable CMS fixture — no user key or production DB involved."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from voodoo_casebook import CasebookStore, IntegrityError
from voodoo_casebook.backups import create_backup
from voodoo_casebook.offline_transfer import (
    export_encrypted_snapshot, restore_encrypted_snapshot,
)


class OfflineEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cbk-cms-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.store = CasebookStore(self.base / "live")
        self.sid = self.store.start_session(
            project="synthetic", goal="cryptographic offline recovery",
            session_id="offline-fixture",
        )
        self.store.append(
            session_id=self.sid, event_type="TODO", summary="synthetic",
            event_id="offline-synthetic-event",
        )
        self.snapshot = create_backup(self.store)
        self.cert = self.base / "recipient-cert.pem"
        self.key = self.base / "synthetic-private-key.pem"
        result = subprocess.run(
            ["/usr/bin/openssl", "req", "-new", "-x509", "-newkey", "rsa:2048",
             "-nodes", "-keyout", str(self.key), "-out", str(self.cert),
             "-subj", "/CN=CASEBOOK-UNIT-TEST-ONLY", "-days", "1"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, "OpenSSL synthetic fixture failed")
        self.key.chmod(0o600)
        self.package = self.base / "transfer.cms"

    def export(self):
        return export_encrypted_snapshot(
            self.store, self.snapshot["backup_id"], self.cert, self.package,
        )

    def recover(self, receipt, *, target=None, key=None, cert=None,
                ciphertext_digest=None, plaintext_digest=None, head=None):
        return restore_encrypted_snapshot(
            self.package, cert or self.cert, key or self.key,
            target or self.base / "restore",
            expected_envelope_sha256=ciphertext_digest or receipt["envelope_sha256"],
            expected_database_sha256=plaintext_digest or receipt["database_sha256"],
            expected_head_hash=head or receipt["head_hash"],
        )

    def test_synthetic_encrypt_and_restored_chain_matches(self):
        record=self.export()
        self.assertEqual(record["status"],"ENCRYPTED_PACKAGE_CREATED_NOT_OFF_HOST")
        self.assertFalse(record["off_device_copy_verified"])
        self.assertFalse(record["authenticated_independently"])
        self.assertEqual(stat.S_IMODE(self.package.stat().st_mode),0o600)
        self.assertNotIn(b"SQLite format 3",self.package.read_bytes()[:100])
        result=self.recover(record)
        self.assertEqual(result["status"],"OFFLINE_DECRYPT_AND_RESTORE_VERIFIED_ISOLATED")
        self.assertEqual(result["event_count"],2)
        self.assertEqual(result["head_hash"],self.store.verify()["head_hash"])
        self.assertFalse(result["promoted_to_active"])
        self.assertFalse(result["off_device_restore_verified"])
        self.assertEqual(CasebookStore(self.base/"restore").get_session(self.sid),
                         self.store.get_session(self.sid))
        self.assertEqual(self.store.verify()["event_count"],2)

    def test_wrong_independent_envelope_digest_rejected(self):
        record=self.export()
        with self.assertRaisesRegex(IntegrityError,"pinned digest"):
            self.recover(record,ciphertext_digest="0"*64)
        self.assertFalse((self.base/"restore").exists())

    def test_wrong_independent_plaintext_digest_rolls_back(self):
        record=self.export()
        with self.assertRaisesRegex(IntegrityError,"decrypted snapshot"):
            self.recover(record,plaintext_digest="0"*64)
        self.assertFalse((self.base/"restore").exists())

    def test_wrong_head_pin_rejected_after_full_chain_verify(self):
        record=self.export()
        with self.assertRaisesRegex(IntegrityError,"independent pin"):
            self.recover(record,head="f"*64)
        self.assertFalse((self.base/"restore").exists())

    def test_truncated_envelope_fails_before_restore(self):
        record=self.export()
        self.package.write_bytes(self.package.read_bytes()[:60])
        with self.assertRaisesRegex(IntegrityError,"pinned digest"):
            self.recover(record)
        self.assertFalse((self.base/"restore").exists())

    def test_private_key_permissions_enforced(self):
        record=self.export()
        self.key.chmod(0o644)
        with self.assertRaisesRegex(PermissionError,"private key"):
            self.recover(record)
        self.assertFalse((self.base/"restore").exists())

    def test_existing_destination_not_overwritten(self):
        record=self.export()
        target=self.base/"existing"
        target.mkdir(mode=0o700)
        (target/"sentinel").write_text("unchanged")
        with self.assertRaises(FileExistsError):
            self.recover(record,target=target)
        self.assertEqual((target/"sentinel").read_text(),"unchanged")

    def test_wrong_private_key_decryption_rolls_back(self):
        record=self.export()
        wrong=self.base/"wrong-key.pem"
        wrong_cert=self.base/"wrong-cert.pem"
        p=subprocess.run(
            ["/usr/bin/openssl","req","-new","-x509","-newkey","rsa:2048",
             "-nodes","-keyout",str(wrong),"-out",str(wrong_cert),
             "-subj","/CN=WRONG-SYNTHETIC","-days","1"],
            capture_output=True,text=True,timeout=10,
        )
        self.assertEqual(p.returncode,0)
        wrong.chmod(0o600)
        with self.assertRaisesRegex(IntegrityError,"CMS"):
            self.recover(record,key=wrong)
        self.assertFalse((self.base/"restore").exists())

    def test_invalid_certificate_prevents_export(self):
        self.cert.write_text("not a real recipient certificate")
        with self.assertRaises((IntegrityError, ValueError)):
            self.export()
        self.assertFalse(self.package.exists())

    def test_export_existing_path_does_not_overwrite(self):
        self.package.write_text("sentinel")
        with self.assertRaises(FileExistsError):
            self.export()
        self.assertEqual(self.package.read_text(),"sentinel")

    def test_cli_export_decrypt_roundtrip_using_fixture(self):
        export=subprocess.run(
            [sys.executable,"-B","-m","voodoo_casebook.offline_transfer","export",
             "--data-root",str(self.store.root),
             "--backup-id",self.snapshot["backup_id"],
             "--recipient-cert",str(self.cert),"--to",str(self.package)],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,text=True,timeout=12,
        )
        self.assertEqual(export.returncode,0,export.stderr+export.stdout)
        record=json.loads(export.stdout)
        target=self.base/"cli-restored"
        import_args=[
            sys.executable,"-B","-m","voodoo_casebook.offline_transfer","restore",
            "--package",str(self.package),"--recipient-cert",str(self.cert),
            "--private-key",str(self.key),"--to",str(target),
            "--expected-package-sha256",record["envelope_sha256"],
            "--expected-database-sha256",record["database_sha256"],
            "--expected-head-hash",record["head_hash"],
        ]
        restored=subprocess.run(import_args,
                                cwd=str(Path(__file__).resolve().parents[1]),
                                capture_output=True,text=True,timeout=12)
        self.assertEqual(restored.returncode,0,restored.stderr+restored.stdout)
        self.assertEqual(json.loads(restored.stdout)["event_count"],2)
        self.assertEqual(self.store.verify()["event_count"],2)


if __name__=="__main__":
    unittest.main()
