# VOODOO-CASEBOOK v0.4 — single full verification per write

**Status:** isolated review candidate; NOT merged, deployed or production-adopted.

## Finding

The v0.3 methods append() and start_session() invoked initialize() on every call. For an existing database, initialize() performed a full SQLite integrity/hash-chain audit. The subsequent BEGIN IMMEDIATE transaction repeated the same full audit, yielding two O(n) historical validations per write.

## Change

- New _ensure_initialized() initializes only when the database does not exist.
- append() and start_session() still acquire BEGIN IMMEDIATE and run the existing complete _verify_connection() once, before modifying existing data.
- Public initialize(), verify(), get_session() and backup integrity checks still perform the full audit; no checkpoint or cached hash is trusted.
- No schema migration, event format, private data-root behavior, CASER integration, network provider, or background job is introduced.

Every write into an existing store still scans the entire history. Complexity remains O(n) per append and O(n²) over n consecutive appends. This change reduces duplicated work only. True O(1) append verification requires separately governed trust, attestation, tamper detection and recovery design.

## Measurement and methodology

Baseline source main@710f37129712cb567e97bd7052e8475b3a94c895 extracted from Git into a disposable working directory and compared with the isolated branch review/v0.4-single-verify.

Mac VoodooBook, Python 3.9.6, one consecutive run per variant with the same 160 synthetic append requests, fresh owner-only private SQLite stores in disposable directories outside Git (plus one initial GOAL event). No user evidence was read or copied. Same hardware and method, but order and disk cache are not randomized; NOT a production benchmark.

| Metric | main baseline | v0.4 candidate |
| --- | ---: | ---: |
| Total append duration | 6501.361 ms | 5030.847 ms |
| Median first 20 appends | 28.955 ms | 25.284 ms |
| Median last 20 appends | 51.450 ms | 36.871 ms |
| p95 append | 52.633 ms | 37.278 ms |
| Full integrity after final write | PASS | PASS |

Throughput speedup: 1.292x; last-20 latency speedup: 1.395x. Single-run evidence only; no confidence interval, 10k-event workload or production SLO assertion.

A reproducible bounded benchmark that generates no tracked data:

    python3 -B -m benchmarks.append_latency --data-parent /Users/eimyna/0_EVIDENCE/VOODOO-CASEBOOK --events 160

It creates synthetic data in a private temporary directory under the explicit evidence parent and removes that directory after the run.

## Security regression gates

- Exactly one full _verify_connection() call on an existing-store append, session start and identical retry.
- Deep historical tampering, a missing append-only trigger and a corrupted head must reject writes even after the optimization.
- A missing store must still initialize without inventing a session.
- All existing integrity, crash recovery, idempotency, WAL concurrency, backup/restore and read-snapshot regression tests must pass.
- The exact candidate GitHub PR must pass Linux CI before any adoption.

## Remaining limitations

- Full O(n) audit still runs for every write. O(n²) cumulative performance at 10k/100k records remains an open scale gate.
- The database and local backups are plaintext and on the same host; encrypted off-device recovery remains unverified.
- Hash chain has no external authenticated anchor and there is no independently attested verifier.
- CASER/CASTER provider execution and authority are not part of this proposal.
- Explicit source merge and production deployment are separate protected decisions.

This candidate does not establish production stability.
