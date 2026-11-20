# VOODOO-CASEBOOK

Local-first, governed work-memory and evidence journal for the VOODOO ecosystem.

> **Status: INITIAL REPOSITORY ONLY.** No runtime, persistent database, ChatGPT memory connector, verified backup or agent integration is implemented yet. A README is not an operational acceptance result.

## Scope

VOODOO-CASEBOOK is an independent project. Its intended responsibilities are:

- Capture session-scoped goals, decisions, issues, results and evidence references.
- Preserve source provenance and distinguish observations from claims or hypotheses.
- Support resumable handoffs across authorized tools and agents.
- Verify record integrity without claiming that a stored claim is independently true.

**Authority boundary:** CASEBOOK records and retrieval **never grant permission**, execute work, or promote notes to canonical project truth. Project repositories, explicit governance and verified runtime evidence remain authoritative.

## Source vs. data

This repository is for **source code, schemas, tests and public-safe documentation only**.

Do not commit real session events, databases, personal information, access tokens, local secrets, backups, agent transcripts or private evidence.

Future local layout:

- Source: `~/0_DEV/VOODOO-CASEBOOK`
- Private runtime evidence: `~/0_EVIDENCE/VOODOO-CASEBOOK` (outside Git)

The user-specific machine bootstrap and project governance must be read before working on a local checkout. No other VOODOO repository is automatically authorized for changes.

## First acceptance target

A standard-library Python + SQLite core that can:

1. Append an attributed event to a session.
2. Reopen the same database in a new process and read the exact event.
3. Detect corrupted/changed record content via a verifiable event hash chain.
4. Deny unsafe paths and preserve project/session isolation.
5. Pass negative, restart, recovery and read-only regression tests.

Until these checks are evidenced, persistence is **PROPOSED**, not operational.

## Index / integrations

GitHub repository: https://github.com/eimyroot/VOODOO-CASEBOOK

CASER, CASER-SOURCER and external ChatGPT integrations are future **adapters**, not bundled execution authorities. Indexing source code must never be confused with indexing private CASEBOOK data.

## License

Not yet selected. No license grant should be inferred.
