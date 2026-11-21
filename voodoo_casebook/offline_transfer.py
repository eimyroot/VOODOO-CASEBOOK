"""Offline CMS-encrypted SQLite snapshot transfer; no key generation or agent grant.

This feature requires an independently pinned certificate and off-host stored
expected digests. CMS AES-256-CBC alone does NOT authenticate the ciphertext.
No real recovery claim until an independent off-host machine restores it.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .backups import DATABASE_FILENAME, MANIFEST_NAME, _sha256, verify_backup
from .core import CasebookStore, IntegrityError

HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
OPENSSL = "/usr/bin/openssl"


def _safe_file(path: Path, *, secret: bool = False) -> Path:
    path = Path(path).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("file path must be absolute without traversal")
    if path.is_symlink() or not path.is_file():
        raise ValueError("expected existing regular non-symlink file")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("expected regular file")
    if secret and (info.st_uid != os.getuid() or
                   stat.S_IMODE(info.st_mode) & 0o077):
        raise PermissionError("private key must be owned by caller and owner-only")
    return path


def _safe_parent(path: Path) -> Path:
    path = Path(path).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("destination must be absolute without traversal")
    if "0_DEV" in path.parts:
        raise ValueError("destination cannot be inside 0_DEV")
    parent = path.parent
    if (parent.is_symlink() or not parent.is_dir() or
            parent.resolve() != parent.absolute() or
            parent.stat().st_uid != os.getuid() or
            stat.S_IMODE(parent.stat().st_mode) & 0o077):
        raise PermissionError("destination parent must be a real owner-only directory")
    if path.exists() or path.is_symlink():
        raise FileExistsError("refusing to overwrite destination")
    return path


def _openssl(*arguments: str) -> None:
    if not Path(OPENSSL).is_file():
        raise RuntimeError("system OpenSSL is unavailable")
    result = subprocess.run(
        [OPENSSL, "cms", *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=40,
        check=False,
    )
    if result.returncode != 0:
        # Do not echo the process environment, paths, or crypto error detail.
        raise IntegrityError("offline CMS encryption/decryption failed")


def _pinned_certificate(path: Path, expected_sha256: str) -> tuple[Path, bytes]:
    """Require a separately trusted certificate pin BEFORE any export/import.

    A digest supplied by an attacker alongside a substituted certificate is
    not a meaningful trust anchor. Out-of-band operator verification is a
    required precondition and is not provided by this API.
    """
    if not isinstance(expected_sha256, str) or not HEX_SHA256.fullmatch(expected_sha256):
        raise ValueError("operator-pinned recipient certificate SHA-256 required")
    certificate = _safe_file(path)
    content = certificate.read_bytes()
    if not 100 <= len(content) <= 65536:
        raise ValueError("recipient certificate has invalid size")
    if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), expected_sha256):
        raise IntegrityError("recipient certificate does not match independently approved fingerprint")
    return certificate, content


def export_encrypted_snapshot(
    store: CasebookStore, backup_id: str, recipient_cert: Path, destination: Path,
    *, expected_recipient_cert_sha256: str,
) -> dict[str, Any]:
    """Encrypt a *verified existing* snapshot to a brand-new private CMS file.

    The recipient certificate must be pinned independently; source is an
    existing verified backup, never a direct copy of a live WAL database.
    """
    source_report = verify_backup(store, backup_id)
    certificate, cert_content = _pinned_certificate(
        recipient_cert, expected_recipient_cert_sha256,
    )
    destination = _safe_parent(destination)
    if destination == store.root or store.root in destination.parents:
        raise ValueError("encrypted export must be outside active evidence root")
    source = store.root / "backups" / backup_id / DATABASE_FILENAME
    manifest = json.loads(
        (store.root / "backups" / backup_id / MANIFEST_NAME).read_text("utf-8")
    )
    source_digest = manifest["database_sha256"]

    if not hasattr(os, "O_NOFOLLOW"):
        raise OSError("O_NOFOLLOW required for offline export")
    temporary = destination.parent / (".cbk-cms-" + uuid.uuid4().hex + ".tmp")
    pinned_cert = destination.parent / (".cbk-recipient-" + uuid.uuid4().hex + ".pem")
    try:
        handle = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         os.O_NOFOLLOW, 0o600)
        os.close(handle)
        handle = os.open(str(pinned_cert), os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         os.O_NOFOLLOW, 0o600)
        with os.fdopen(handle, "wb") as stream:
            stream.write(cert_content)
            stream.flush()
            os.fsync(stream.fileno())
        # OpenSSL reads an owner-only frozen COPY of the pinned bytes, not the
        # original certificate path that could be swapped after preflight.
        _openssl(
            "-encrypt", "-binary", "-aes256", "-in", str(source),
            "-out", str(temporary), "-outform", "DER", str(pinned_cert),
        )
        if _sha256(source) != source_digest:
            raise IntegrityError("backup snapshot changed during encrypted export")
        again = verify_backup(store, backup_id)
        if again != source_report:
            raise IntegrityError("backup verification changed during export")
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        # Atomic hardlink fails instead of overwriting a destination
        # created by a competing process after our initial path check.
        os.link(str(temporary), str(destination))
        fd = os.open(str(destination), os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return {
            "status": "ENCRYPTED_PACKAGE_CREATED_NOT_OFF_HOST",
            "backup_id": backup_id,
            "envelope_path": str(destination),
            "envelope_sha256": _sha256(destination),
            "database_sha256": source_digest,
            "head_hash": source_report["head_hash"],
            "head_seq": source_report["head_seq"],
            "event_count": source_report["event_count"],
            "recipient_cert_file_sha256": hashlib.sha256(cert_content).hexdigest(),
            "format": "CMS_DER_AES_256_CBC_ENVELOPED_DATA",
            "authenticated_independently": False,
            "off_device_copy_verified": False,
            "private_key_accessed": False,
        }
    finally:
        if temporary.exists():
            temporary.unlink()
        if pinned_cert.exists():
            pinned_cert.unlink()


def restore_encrypted_snapshot(
    envelope: Path, recipient_cert: Path, private_key: Path, destination: Path,
    *, expected_envelope_sha256: str, expected_database_sha256: str,
    expected_head_hash: str, expected_recipient_cert_sha256: str,
) -> dict[str, Any]:
    """Decrypt and verify to a NEW private root, without changing live data.

    Digests/head must be supplied from a separate trusted transfer record.
    Never treat an untrusted metadata file shipped with CMS as an anchor.
    """
    for value in (expected_envelope_sha256, expected_database_sha256,
                  expected_head_hash):
        if not isinstance(value, str) or not HEX_SHA256.fullmatch(value):
            raise ValueError("required independently pinned SHA-256 is invalid")
    encrypted = _safe_file(envelope)
    certificate, cert_content = _pinned_certificate(
        recipient_cert, expected_recipient_cert_sha256,
    )
    key = _safe_file(private_key, secret=True)
    target = _safe_parent(destination)
    if _sha256(encrypted) != expected_envelope_sha256:
        raise IntegrityError("encrypted transfer differs from pinned digest")

    target.mkdir(mode=0o700, exist_ok=False)
    try:
        output = target / DATABASE_FILENAME
        fd = os.open(str(output), os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW, 0o600)
        os.close(fd)
        pinned_cert = target / ".pinned-recipient-cert.pem"
        fd = os.open(str(pinned_cert), os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(cert_content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            _openssl(
                "-decrypt", "-binary", "-inform", "DER", "-in", str(encrypted),
                "-recip", str(pinned_cert), "-inkey", str(key),
                "-out", str(output),
            )
        finally:
            pinned_cert.unlink()

        if _sha256(output) != expected_database_sha256:
            raise IntegrityError("decrypted snapshot differs from pinned digest")
        report = CasebookStore(target).verify()
        if report["head_hash"] != expected_head_hash:
            raise IntegrityError("restored head differs from independent pin")
        return {
            "status": "OFFLINE_DECRYPT_AND_RESTORE_VERIFIED_ISOLATED",
            "restore_root": str(target),
            "database_sha256": expected_database_sha256,
            **report,
            "promoted_to_active": False,
            "off_device_restore_verified": False,
            "caller_key_custody_unverified": True,
        }
    except Exception:
        shutil.rmtree(str(target))
        raise


def main(argv: list[str] | None = None) -> int:
    """Human-invoked offline transport CLI, no daemon or agent integration."""
    parser = argparse.ArgumentParser(
        description="CASEBOOK offline encrypted transfer, operator only",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Encrypt an existing verified backup")
    export.add_argument("--data-root", type=Path, required=True)
    export.add_argument("--backup-id", required=True)
    export.add_argument("--recipient-cert", type=Path, required=True)
    export.add_argument("--expected-recipient-cert-sha256", required=True)
    export.add_argument("--to", type=Path, required=True)
    restore = commands.add_parser("restore", help="Decrypt to a NEW private root only")
    restore.add_argument("--package", type=Path, required=True)
    restore.add_argument("--recipient-cert", type=Path, required=True)
    restore.add_argument("--expected-recipient-cert-sha256", required=True)
    restore.add_argument("--private-key", type=Path, required=True)
    restore.add_argument("--to", type=Path, required=True)
    restore.add_argument("--expected-package-sha256", required=True)
    restore.add_argument("--expected-database-sha256", required=True)
    restore.add_argument("--expected-head-hash", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            record = export_encrypted_snapshot(
                CasebookStore(args.data_root), args.backup_id,
                args.recipient_cert, args.to,
                expected_recipient_cert_sha256=args.expected_recipient_cert_sha256,
            )
        else:
            record = restore_encrypted_snapshot(
                args.package, args.recipient_cert, args.private_key,
                args.to,
                expected_envelope_sha256=args.expected_package_sha256,
                expected_database_sha256=args.expected_database_sha256,
                expected_head_hash=args.expected_head_hash,
                expected_recipient_cert_sha256=args.expected_recipient_cert_sha256,
            )
    except (ValueError, LookupError, IntegrityError, PermissionError,
            OSError, subprocess.SubprocessError) as exc:
        # Never print private key bytes, passphrases or OpenSSL stderr.
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        return 2
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
