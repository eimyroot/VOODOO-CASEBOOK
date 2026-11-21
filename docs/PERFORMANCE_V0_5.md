# VOODOO-CASEBOOK v0.5 — atomic batch throughput candidate

**Status:** isolated stacked review branch based on v0.4 Draft PR #5, not merged and not deployed.

## Problem and profiler evidence

The v0.4 implementation performs a correct full SQLite integrity / event SHA-256 audit per write, opens a SQLite connection, writes and then closes. On a VoodooBook synthetic dataset with 141 existing events, profiling 12 more individual writes produced 607.955 ms end-to-end, including 205 ms recorded in SQLite connection closing and 212 ms cumulative inside the full verification function. These figures overlap with other profile entries; do not sum them as disjoint categories.

Repeated connection close/checkpoint and repeated full-history validation are significant throughput costs. Disabling the verification would weaken tamper detection; this candidate does not do that.

## v0.5 API contract

Use the Python API only. There is **no provider write adapter, CASER integration, network endpoint, background process, or CLI batch action**:

```python
store.append_batch(
    session_id=sid,
    events=[
        {"type": "TODO", "summary": "First", "event_id": "stable_first"},
        {"type": "EVIDENCE", "summary": "Second", "event_id": "stable_second"},
    ],
)
```

Each event requires `type` and `summary` and optionally accepts `event_id`, `source`, `status`, `metadata` and `evidence_refs`; defaults and bounds match existing `append()`. Batches contain **1–32 events belonging to one already-existing session**.

One `BEGIN IMMEDIATE` transaction:
1. Prevalidates all event requests (types, sizes, ID uniqueness).
2. Acquires the SQLite writer lock and performs the **same complete SQLite physical integrity check, trigger check and full historic SHA-256 chain scan**.
3. Applies each event or verifies a matching idempotent replay, in caller-specified order; newly appended events advance the predecessor hash inside the same transaction.
4. Commits all results together, or rolls back the entire batch if one request fails, even after earlier inserts.
5. Closes the database connection.

Other SQLite writers cannot interleave between validation and commit because of the single `BEGIN IMMEDIATE` transaction. No cached trust anchors, shortened audit, schema migration or downgrade in synchronous WAL durability is introduced.

**Idempotency:** supply stable `event_id` values to replay safely after response loss; a reused ID with a different payload or session causes a complete rollback. Missing IDs generate new IDs (exactly like `append()`), so retrying without stable IDs is not idempotent.

The existing single-event `append()` API and its contract remain unchanged.

## Controlled benchmark

Same VoodooBook, macOS 15.8.1, Python 3.9.6, isolated disposable SQLite databases outside Git. Each variant wrote **160 equivalent ordered synthetic TODO records** after an initial GOAL. Compare 160 individual `append()` calls with five atomic `append_batch()` calls of 32 events. Order was alternated in two sequential trials; all results were checked via full `verify()` and exact event IDs/order.

| Trial | Single-event calls | Batches of 32 | Ratio |
| --- | ---: | ---: | ---: |
| Single then batch | 5,927.471 ms | 175.559 ms | 33.763× |
| Batch then single | 11,679.978 ms | 173.842 ms | 67.187× |

Strong host/load variability affects single-event timing; **do not interpret these results as a guaranteed production speedup**. They compare two different transaction/atomicity patterns, not identical request latency. One batch takes roughly 35 ms in this dataset, not 1 ms. Per-event amortized overhead is much lower because 32 events share a single connection, audit and transaction.

Reproduce without external dependencies:

```sh
python3 -B -m benchmarks.append_latency --data-parent /Users/eimyna/0_EVIDENCE/VOODOO-CASEBOOK --events 160 --mode single
python3 -B -m benchmarks.append_latency --data-parent /Users/eimyna/0_EVIDENCE/VOODOO-CASEBOOK --events 160 --mode batch --batch-size 32
```

Both benchmarks create a temporary synthetic owner-only data root and remove it after running. They do not read the existing evidence database.

## Security and operational gates

- Verify exactly one full-chain audit per atomic batch, regardless of event count.
- Negative regression: a forged historical event, removed trigger, or later conflicting replay blocks the whole batch.
- Interruption after a partial set of inserts must roll back all inserts.
- Existing single append, crash recovery, idempotency, WAL concurrency and backup/restore tests must continue passing.
- The existing cold-start concurrency availability defect is inherited; it is **not** fixed here. For now, initialize once before concurrent batching; do not claim this solves cross-process first-write safety.
- Every remaining event still passes through the unchanged `_append_transaction()` hash-link creation. The audit of **previous persisted history** occurs before the atomic group, not for each newly built event within that group.
- Production acceptance remains blocked on formal independent review, encrypted off-device recovery, external integrity anchors and realistic load tests.

## Complexity and scope

For n historic events and a batch of b events, the full prior-history validation is O(n), and the batch's new event work is O(b). O(n²) over long-running unbatched histories remains; batching only amortizes this cost across bounded, atomic groups. The feature is **opt-in**, not an autonomous production runtime.
