# VOODOO-CASEBOOK

Independent, local-first, **governed session memory** for the VOODOO ecosystem.

> **v0.1 REVIEW CANDIDATE:** A Python standard-library SQLite event store, CLI and negative test suite exist on the review branch. This is NOT a CASER integration, agent executor, independently witnessed evidence vault, production release, or an authorization system.

## Core capabilities (v0.1)

- Create a session with an immutable initial GOAL event.
- Append attributed events with allowed CASEBOOK types (e.g. DECISION, TODO, EVIDENCE, BLOCKER, RESULT).
- Persist in a private SQLite database, reopen across processes, and view a session.
- Keep a global monotonically sequenced event chain, SHA-256 over canonical JSON, an anchored head **inside the same database**, and append-only SQL triggers.
- Fail closed on corrupted records, broken session/project bindings, incorrect head hashes, unsafe data locations and invalid event contracts.
- Use SQLite WAL, full synchronous writes and a single transaction for each new event.
- Use no paid services, non-standard packages, network dependencies, or additional permanent daemons.

### Separation of responsibilities

**GitHub repository = source code, schemas, tests, public documentation.**

**Private evidence directory = actual session data, never committed.**

Default on VoodooBook:

- Source: `/Users/eimyna/0_DEV/VOODOO-CASEBOOK`
- Private database: `/Users/eimyna/0_EVIDENCE/VOODOO-CASEBOOK/casebook.sqlite3`

The parent evidence directory must be owner-only (`0700`), and the database is set to `0600`. Symlinked data roots, unsafe traversal and any destination inside this source repository are rejected. The database is **plaintext**: do not record passwords, tokens or unnecessary sensitive information. Whole-device and off-device encryption/key-custody are not proved by this prototype.

## Quick start

From the cloned repository root (Python 3.9+):

```sh
python3 -B -m voodoo_casebook init
python3 -B -m voodoo_casebook start --project demo --goal "Resume a governed session"
python3 -B -m voodoo_casebook append --session ses_REPLACE_WITH_RETURNED_ID --type DECISION --summary "Use local SQLite"
python3 -B -m voodoo_casebook show --session ses_REPLACE_WITH_RETURNED_ID
python3 -B -m voodoo_casebook verify
```

To use a separate private, owner-only directory for a controlled acceptance test, pass `--data-root /absolute/path/outside/repo` **before** the subcommand. Do not put private data under `0_DEV`.

Package API:

```python
from pathlib import Path
from voodoo_casebook import CasebookStore

store = CasebookStore(Path.home() / "0_EVIDENCE" / "VOODOO-CASEBOOK")
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
```

`VERIFIED_INTEGRITY` attests only **internal journal structure**, not the truth of notes, evidence authenticity, cross-machine durability, or an independently signed verifier receipt.

## Validation

```sh
python3 -B -m unittest discover -s tests -v
git diff --check
```

Tests include separate-process reopening, independent hash recomputation, tamper/failure cases, simultaneous append operations and SQLite online-backup/restore verification. No provider WRITE/deploy/release action is exposed.

## Non-authority rule

CASEBOOK does not authorize execution, grant approval, create or merge pull requests, modify other project repositories, or promote entries to canonical state.

CASER is a future orchestration adapter; CASER-SOURCER governs provenance and canonical source classification; Voodoo-One governs authorization; an independent verifier must separately confirm execution outcomes.

## Integrity limits

See [docs/INTEGRITY.md](docs/INTEGRITY.md). The v0.1 hash chain **is not cryptographically tamper-proof against an attacker who can edit the entire SQLite database and recompute all hashes**. The head hash lives in that same database; independent anchoring, external verifier identity and authenticated/encrypted backups remain future gates.

## Index and project status

Machine-readable repository index: [project-index.json](project-index.json).
Repository: https://github.com/eimyroot/VOODOO-CASEBOOK.

No license has yet been selected; do not infer permission to reuse the source beyond what applicable law allows.
