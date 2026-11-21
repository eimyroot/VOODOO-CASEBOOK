# VOODOO-CASEBOOK

Independent, local-first, **governed session memory** for the VOODOO ecosystem.

> **v0.6 SOURCE ADOPTED IN MAIN; v0.7 OFFLINE ENVELOPE REVIEW CANDIDATE:** Local-only verified SQLite journal, safe cross-process startup, bounded atomic batches and isolated manual snapshot restore. v0.7 adds a human-invoked encrypted CMS transfer prototype with independently pinned digests, but real off-host recovery and key custody are NOT verified. No CASER agent grant, background service, production export, or deployment.

## Core capabilities (v0.1)

- Create a session with an immutable initial GOAL event.
- Append attributed events with allowed CASEBOOK types (e.g. DECISION, TODO, EVIDENCE, BLOCKER, RESULT).
- Persist in a private SQLite database, reopen across processes, and view a session.
- Keep a global monotonically sequenced event chain, SHA-256 over canonical JSON, an anchored head **inside the same database**, and append-only SQL triggers.
- Fail closed on corrupted records, broken session/project bindings, incorrect head hashes, unsafe data locations and invalid event contracts.
- Use SQLite WAL, full synchronous writes and a single transaction for each new event.
- Opt-in Python `append_batch()` for 1–32 events in one pre-verified atomic transaction (included in v0.5 main).
- v0.6 main: bounded cross-process first-initialization lock; explicit owner-only restore into a brand-new root, never live overwrite or activation.
- v0.7 review-only: offline CMS encrypted transfer of an existing VERIFIED snapshot. The recipient certificate SHA-256 MUST be independently approved and supplied **before export and restore**; ciphertext, plaintext and chain-head pins are also mandatory. NOT authenticated encryption or proved off-device restore.
- Use no paid services, non-standard packages, network dependencies, or additional permanent daemons.

### Separation of responsibilities

**GitHub repository = source code, schemas, tests, public documentation.**

**Private evidence directory = actual session data, never committed.**

Configured data root on VoodooBook:

- Source: `/Users/eimyna/0_DEV/VOODOO-CASEBOOK`
- Private database: `/Users/eimyna/0_EVIDENCE/VOODOO-CASEBOOK/casebook.sqlite3`

The selected evidence directory must be owner-only (`0700`), and the database is set to `0600`. Symlinked data roots, unsafe traversal and any destination inside this source repository are rejected. The database is **plaintext**: do not record passwords, tokens or unnecessary sensitive information. Whole-device and off-device encryption/key-custody are not proved by this prototype.

## Quick start

From the cloned repository root (Python 3.9+):

```sh
export VOODOO_CASEBOOK_DATA_ROOT=/Users/eimyna/0_EVIDENCE/VOODOO-CASEBOOK
python3 -B -m voodoo_casebook init
python3 -B -m voodoo_casebook start --project demo --goal "Resume a governed session"
python3 -B -m voodoo_casebook append --session ses_REPLACE_WITH_RETURNED_ID --type DECISION --summary "Use local SQLite" --event-id cbk_unique_replay_token
python3 -B -m voodoo_casebook show --session ses_REPLACE_WITH_RETURNED_ID
python3 -B -m voodoo_casebook verify
python3 -B -m voodoo_casebook backup
python3 -B -m voodoo_casebook backup-verify --backup-id BACKUP_ID_FROM_PREVIOUS_OUTPUT
```

**v0.2 data-root rule:** An explicit absolute `--data-root` or `VOODOO_CASEBOOK_DATA_ROOT` is mandatory. The CLI never infers `Path.home()`, which was unsafe under CyberBridge's temporary HOME. To use another owner-only directory, place `--data-root /absolute/private/path` before the command. Never put private data under `0_DEV`.

For an operation that may be retried after a timeout, keep the same `--event-id` **and all request fields**. An identical retry returns the original event; conflicting reuse is rejected. `start --session-id` is likewise idempotent for the exact same project and goal.

Package API:

```python
from pathlib import Path
from voodoo_casebook import CasebookStore

store = CasebookStore(Path("/Users/eimyna/0_EVIDENCE/VOODOO-CASEBOOK"))
sid = store.start_session(project="demo", goal="Verify persistence")
store.append(
    session_id=sid,
    event_type="DECISION",
    summary="Keep data outside Git",
    source="user",
    evidence_refs=["https://github.com/eimyroot/VOODOO-CASEBOOK"],
    metadata={"claim_status": "UNVERIFIED"},
)
assert store.verify()["verdict"] == "VERIFIED_INTEGRITY"
assert len(CasebookStore(store.root).get_session(sid)["events"]) == 2
# From v0.5 main: one full audit, one atomic transaction:
store.append_batch(
    session_id=sid,
    events=[
        {"type": "TODO", "summary": "one", "event_id": "cbk_unique_one"},
        {"type": "RESULT", "summary": "two", "event_id": "cbk_unique_two"},
    ],
)
```

`VERIFIED_INTEGRITY` attests only **internal journal structure**, not the truth of notes, evidence authenticity, cross-machine durability, or an independently signed verifier receipt.

## Validation

```sh
python3 -B -m unittest discover -s tests -v
git diff --check
```

Synthetic benchmark (explicit private evidence parent; never use a real database):

```sh
python3 -B -m benchmarks.append_latency --data-parent /Users/eimyna/0_EVIDENCE/VOODOO-CASEBOOK --events 160
python3 -B -m benchmarks.append_latency --data-parent /Users/eimyna/0_EVIDENCE/VOODOO-CASEBOOK --events 160 --mode batch --batch-size 32
```

Tests include separate-process reopening, crash-before-commit rollback, response-lost-after-commit retry, independent hash recomputation, tamper/trigger failures, simultaneous append operations, snapshot verification and backup/restore comparison. v0.6 adds cross-process concurrent cold-start and isolated restore tests. v0.7 adds synthetic recipient-certificate CMS transfer and independently pinned negative crypto recovery tests. The separate cert-pin hardening candidate denies certificate substitution before and after preflight. See [agent isolation gate](docs/AGENT_ISOLATION_GATE.md), [v0.7 offline transfer limitations](docs/OFFLINE_RECOVERY_V0_7.md), [v0.6 local operations runbook](docs/LOCAL_OPERATIONS_V0_6.md), [v0.5 batch performance](docs/PERFORMANCE_V0_5.md), [v0.4 performance](docs/PERFORMANCE_V0_4.md), and [v0.2 recovery boundaries](docs/RELIABILITY.md). No provider WRITE/deploy/release action is exposed.

## Non-authority rule

CASEBOOK does not authorize execution, grant approval, create or merge pull requests, modify other project repositories, or promote entries to canonical state.

CASER is a future orchestration adapter; CASER-SOURCER governs provenance and canonical source classification; Voodoo-One governs authorization; an independent verifier must separately confirm execution outcomes.

## Integrity limits

See [docs/INTEGRITY.md](docs/INTEGRITY.md). The local hash chain **is not cryptographically tamper-proof against an attacker who can edit the entire SQLite database and recompute all hashes**. The head hash lives in that same database; independent anchoring, external verifier identity, authenticated envelope encryption and genuine off-host key custody/restoration remain future gates. The v0.7 CMS prototype provides confidentiality but is NOT AEAD without independently pinned digests.

## Index and project status

Machine-readable repository index: [project-index.json](project-index.json).
Repository: https://github.com/eimyroot/VOODOO-CASEBOOK.

No license has yet been selected; do not infer permission to reuse the source beyond what applicable law allows.
