# v0.2 integrity, trust and recovery contract

**Scope:** local, single-writer-serialized SQLite event journal used only for work memory and evidence pointers.

## What is checked

1. SQLite `PRAGMA integrity_check` for physical consistency.
2. Versioned `meta` record with the accepted schema version and head tuple.
3. Monotonically increasing contiguous sequence numbers and SHA-256 predecessor links.
4. Independent canonical-JSON digest recomputation for every stored event.
5. Existing session ID, matching project binding and immutable initial GOAL record.
6. Append-only SQL UPDATE/DELETE rejection for events and sessions.
7. Strict event type/source/status contracts and bounded JSON data fields.
8. Read-only verification and read-only session retrieval (no provider actions).

## Boundaries

- A successful digest chain is **not evidence that a statement is true**.
- SHA-256 is **not keyed**. An attacker with full database write/replace access can rewrite records and recompute the chain and the head. External anchoring or signatures would be required to detect that.
- A private file is **not encrypted**. Avoid sensitive notes; rely on explicit device/disk encryption and future tested off-device key handling where required.
- Raw SQLite file copying while in WAL mode is not a safe backup protocol. Use `sqlite3.Connection.backup()` for a coherent snapshot.
- A successful local restore proves recovery of that one tested snapshot only, not off-device backup availability.
- The service has no network API, cross-user permissions, multi-device sync or live independent verifier.
- Session/project labels partition API queries, **not operating-system users**: the local process has access to the whole database. Do not share this database with mutually untrusted users.

## Recovery acceptance

1. Produce a SQLite online backup into a new owner-only evidence directory.
2. Open a distinct CasebookStore instance against the restored file.
3. Verify equal session and event counts, all hashes and the exact head digest.
4. Compare the returned session/events against the source.
5. Leave the original untouched; report failures as BLOCKED.

## Privacy and source-control rules

Keep real records, `*.sqlite3`, `*.db`, `*.jsonl`, backups, secrets and transcripts out of the public repository. The `.gitignore` helps prevent accidental staging but does not prevent deliberate `git add -f` or other exfiltration; review commits before publication.

## Next gates

- Independently anchored journal-head checkpoint and verified external digest custody.
- Separate authorization/identity boundary for any multi-user or network access.
- Encrypted backup with off-device restore and key recovery test.
- CASER adapter with policy-based approval, explicit authentication and negative tests.
- Independent verifier receipt tied to exact evidence and source SHA.

**No stage above is implemented or authorized by the existence of v0.1.**

## v0.2 additions

- Explicit data-root selection is mandatory in the CLI; no temporary HOME inference.
- Retried session and event IDs are idempotent only for identical request content.
- Missing append-only triggers are rejected on reopening existing databases; no silent schema repair.
- Verified on-demand SQLite snapshots and SHA-256 manifests are stored locally outside Git.
- A local backup manifest does not anchor the hash chain independently and is not encrypted or off-host.
- No automatic scheduling, rotation, pruning, or production recovery action is enabled.

See [Reliability and local recovery](RELIABILITY.md) for crash tests and remaining gates.
