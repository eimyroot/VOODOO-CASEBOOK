"""Verified SQLite online snapshots; private local storage, no external custody."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from .core import CasebookStore, IntegrityError, DATABASE_FILENAME, canonical_json, utc_now

BACKUP_SCHEMA = "voodoo-casebook/backup-manifest/v1"
BACKUP_ID = re.compile(r"[0-9a-f]{32}\Z")
BACKUP_DIR = "backups"
MANIFEST_NAME = "manifest.json"


def _owner_only(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("backup directory must be a real directory")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise PermissionError("backup directory must be owner-only")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _backup_root(store: CasebookStore, *, create: bool) -> Path:
    store._guard(create=False)
    root = store.root / BACKUP_DIR
    if root.is_symlink():
        raise ValueError("backup root must not be a symlink")
    if not root.exists():
        if not create:
            raise FileNotFoundError("backup root has not been created")
        root.mkdir(mode=0o700)
        _fsync_dir(store.root)
    _owner_only(root)
    return root


def verify_backup(store: CasebookStore, backup_id: str) -> dict[str, Any]:
    """Verify snapshot bytes, manifest and full CASEBOOK chain; never overwrite live DB."""
    if not isinstance(backup_id, str) or not BACKUP_ID.fullmatch(backup_id):
        raise ValueError("unsafe backup ID")
    snapshot = _backup_root(store, create=False) / backup_id
    _owner_only(snapshot)
    manifest_path = snapshot / MANIFEST_NAME
    database = snapshot / DATABASE_FILENAME
    if manifest_path.is_symlink() or database.is_symlink():
        raise ValueError("backup files must not be symlinks")
    if not manifest_path.is_file() or not database.is_file():
        raise IntegrityError("backup snapshot or manifest is missing")
    if stat.S_IMODE(manifest_path.stat().st_mode) & 0o077:
        raise PermissionError("backup manifest is not owner-only")
    if stat.S_IMODE(database.stat().st_mode) & 0o077:
        raise PermissionError("backup database is not owner-only")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise IntegrityError("backup manifest is not readable JSON") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != BACKUP_SCHEMA
        or manifest.get("backup_id") != backup_id
    ):
        raise IntegrityError("backup manifest contract mismatch")
    if manifest.get("database_sha256") != _sha256(database):
        raise IntegrityError("backup snapshot digest mismatch")
    verified = CasebookStore(snapshot).verify()
    if (
        verified["head_hash"] != manifest.get("head_hash")
        or verified["head_seq"] != manifest.get("head_seq")
        or verified["session_count"] != manifest.get("session_count")
    ):
        raise IntegrityError("backup manifest and CASEBOOK head disagree")
    return {"status": "BACKUP_VERIFIED", "backup_id": backup_id, **verified}


def create_backup(store: CasebookStore) -> dict[str, Any]:
    """Create an atomic owner-only SQLite online backup and independently reopen it.

    Backup verification only proves this local snapshot. The manifest is not
    signed and lives on the same host; it is not off-device disaster recovery.
    """
    store.verify()  # fail closed before creating backup artifacts
    backups = _backup_root(store, create=True)
    backup_id = uuid.uuid4().hex
    destination = backups / backup_id
    destination.mkdir(mode=0o700)
    _fsync_dir(backups)
    try:
        temporary = destination / (DATABASE_FILENAME + ".tmp")
        complete = destination / DATABASE_FILENAME
        with closing(store._connect(write=False)) as source:
            with closing(sqlite3.connect(str(temporary), isolation_level=None)) as target:
                source.backup(target)
        os.chmod(temporary, 0o600)
        _fsync_file(temporary)
        os.replace(str(temporary), str(complete))
        _fsync_dir(destination)
        report = CasebookStore(destination).verify()
        manifest = {
            "schema": BACKUP_SCHEMA,
            "backup_id": backup_id,
            "created_at": utc_now(),
            "database_sha256": _sha256(complete),
            "head_seq": report["head_seq"],
            "head_hash": report["head_hash"],
            "session_count": report["session_count"],
        }
        temp_manifest = destination / (MANIFEST_NAME + ".tmp")
        fd = os.open(str(temp_manifest), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(canonical_json(manifest) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temp_manifest), str(destination / MANIFEST_NAME))
        _fsync_dir(destination)
        _fsync_dir(backups)
        result = verify_backup(store, backup_id)
        return {**result, "directory": str(destination), "database_sha256": manifest["database_sha256"]}
    except Exception:
        # Only our freshly generated, unadvertised snapshot directory is removed.
        shutil.rmtree(str(destination))
        raise
