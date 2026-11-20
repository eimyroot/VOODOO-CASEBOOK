# VOODOO-CASEBOOK v0.2 — Reliability and local recovery

**Status:** review candidate; no release, CASER integration, off-device backup or unattended scheduler.

## Authority and source policy
- Code and tests belong in the public GitHub repository.
- Real events, snapshots and receipts belong in owner-only evidence storage outside Git.
- This tool does not authorize execution, provider WRITE, merging, deployment or canonical promotion.

## Data root and idempotency

The CLI requires an explicit absolute --data-root or VOODOO_CASEBOOK_DATA_ROOT. It fails closed when neither is provided instead of deriving the temporary HOME used by CyberBridge.

A caller supplies event_id as a durable retry identity. A request with the same ID and **identical** session, type, summary, source, status, references and metadata returns the original event and digest. Conflicting reuse fails without appending. Without an explicit event_id, a new ID is generated: retries are **not** automatically deduplicated.

Starting a session with an explicit session_id is idempotent only for its exact original project and goal.

## Database safety

1. Event writes use one BEGIN IMMEDIATE transaction and COMMIT.
2. Incomplete transactions are rolled back by SQLite after an interrupted process.
3. On reopen, verify schema version, immutable triggers, physical SQLite integrity, complete sequence and hash chain, head and session bindings. Missing triggers are BLOCKED, not silently recreated.
4. Owner-only directory 0700 and database 0600 are checked; wider permissions are rejected.
5. Tampering or integrity errors prevent further application writes; automatic restore is not attempted.

The implementation still scans the complete journal before each append (O(n)). Do not claim high-volume readiness.

## On-demand online snapshots

Use an explicit data root, for example:
- export VOODOO_CASEBOOK_DATA_ROOT=/Users/eimyna/0_EVIDENCE/VOODOO-CASEBOOK
- python3 -B -m voodoo_casebook backup
- python3 -B -m voodoo_casebook backup-verify --backup-id SNAPSHOT_ID

Backup uses SQLite's online backup API, owner-only temporary files, fsync and atomic rename to a randomly named directory beneath the data root. It reopens the snapshot through a distinct CasebookStore and checks its complete chain, head and a SHA-256 snapshot digest.

No existing backup or live database is overwritten. **No unattended backup scheduler, retention or automatic pruning is enabled.**

Local snapshots do not protect against host/disk failure, theft, malware with write access or lost decryption keys. The manifest is unsigned and beside the snapshot. Encryption, off-device custody and independent external hash anchoring remain missing gates.

## Deterministic negative tests

- Crash before COMMIT: no partial event.
- Crash after COMMIT before response: same event_id retry yields one event.
- Conflicting or cross-session ID: reject.
- Missing immutability triggers, widened permissions, malformed hashes, tampered backup and unsafe paths: reject.
- Concurrent appends: one contiguous chain.
- Backup restore: identical head and events; a snapshot remains immutable as live records change.
- Changed temporary HOME: no silent data-root fallback.

Tests are bounded local synthetic evidence, not independent external production attestations.

## Next gate

Independent code/security review on the exact SHA; measured throughput and recovery times; explicit backup retention, encryption and off-device restore testing. Only afterwards proceed to a CASER adapter. Do not claim STABLE before those gates.
