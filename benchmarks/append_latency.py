"""Bounded, repeatable synthetic append benchmark (no private records)."""

from __future__ import annotations

import argparse
import json
import platform
import stat
import statistics
import sys
import tempfile
import time
from pathlib import Path

from voodoo_casebook import CasebookStore


def main() -> int:
    parser = argparse.ArgumentParser(description="CASEBOOK isolated synthetic append benchmark")
    parser.add_argument("--data-parent", type=Path, required=True)
    parser.add_argument("--events", type=int, default=160)
    args = parser.parse_args()
    if not 32 <= args.events <= 5000:
        parser.error("--events must be between 32 and 5000")
    parent = args.data_parent.expanduser()
    if (
        not parent.is_absolute() or ".." in parent.parts
        or not parent.is_dir() or parent.is_symlink()
        or parent.resolve() != parent
        or stat.S_IMODE(parent.stat().st_mode) & 0o077
    ):
        parser.error("--data-parent must be an existing real owner-only absolute directory outside the source repo")
    with tempfile.TemporaryDirectory(prefix="casebook-benchmark-", dir=str(parent)) as td:
        store = CasebookStore(Path(td) / "private")
        sid = store.start_session(project="synthetic-benchmark", goal="Measure exact verified writes")
        durations = []
        for i in range(args.events):
            started = time.perf_counter()
            store.append(
                session_id=sid, event_type="TODO", summary="synthetic {}".format(i),
                event_id="cbk_bench_{}".format(i),
            )
            durations.append(1000 * (time.perf_counter() - started))
        verified = store.verify()
        if verified["event_count"] != args.events + 1:
            raise RuntimeError("benchmark integrity/count mismatch")
        output = {
            "schema": "voodoo-casebook/synthetic-benchmark/v1",
            "python": platform.python_version(),
            "platform": platform.platform(),
            "synthetic": True,
            "events_written": args.events,
            "events_total": verified["event_count"],
            "median_first_20_ms": round(statistics.median(durations[:20]), 3),
            "median_last_20_ms": round(statistics.median(durations[-20:]), 3),
            "p95_ms": round(sorted(durations)[int(0.95 * len(durations)) - 1], 3),
            "total_append_ms": round(sum(durations), 3),
            "integrity": verified["verdict"],
            "production_data_touched": False,
        }
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
