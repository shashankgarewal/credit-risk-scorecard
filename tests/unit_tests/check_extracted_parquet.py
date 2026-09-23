"""
check_extract.py
────────────────
Validates every Parquet file under dataset/extract WITHOUT loading row data.

Uses:
  - pyarrow.parquet.read_metadata() → row count from Parquet footer (O(1), no data read)
  - polars.read_parquet_schema()    → column names + dtypes, no data read

Checks:
  1. All origination files have the same column count.
  2. All performance files have the same column count.
  3. Every column in every file is Utf8 (string).
"""

import sys
from pathlib import Path
from collections import defaultdict

import pyarrow.parquet as pq
import polars as pl

from src.utils.config import ROOT
from src.utils.config import EXTRACT_DIR
# EXTRACT_DIR = ROOT / "dataset/extract_old"


def scan_parquets(dataset_type: str) -> list[dict]:
    """Reads schema + row count from Parquet footer only — no data loaded."""
    type_dir = EXTRACT_DIR / dataset_type
    files = sorted(type_dir.rglob("*.parquet"))

    if not files:
        print(f"  [WARN] No parquet files found under {type_dir}")
        return []

    results = []
    for f in files:
        meta = pq.read_metadata(f)           # reads footer only
        schema = pl.read_parquet_schema(f)   # reads schema only

        non_str = {col: str(dt) for col, dt in schema.items() if dt != pl.Utf8}
        results.append({
            "path": f,
            "vintage": f.parent.name,
            "rows": meta.num_rows,
            "cols": len(schema),
            "non_str": non_str,
        })

    return results


def report(dataset_type: str, results: list[dict]) -> bool:
    ok = True
    col_counts = defaultdict(list)

    print(f"\n{'─'*60}")
    print(f"  {dataset_type.upper()}  ({len(results)} files)")
    print(f"{'─'*60}")
    print(f"  {'Vintage':<18} {'Rows':>12} {'Cols':>6}  {'Non-Str Issues'}")
    print(f"  {'─'*16:<18} {'─'*10:>12} {'─'*4:>6}  {'─'*20}")

    for r in results:
        col_counts[r["cols"]].append(r["vintage"])
        issues = ", ".join(f"{c}:{t}" for c, t in r["non_str"].items()) if r["non_str"] else "✓"
        if r["non_str"]:
            ok = False
        print(f"  {r['vintage']:<18} {r['rows']:>12,} {r['cols']:>6}  {issues}")

    # Column count uniformity check
    print()
    if len(col_counts) == 1:
        n = next(iter(col_counts))
        print(f"  ✅ Column count uniform: {n} cols across all {dataset_type} files.")
    else:
        ok = False
        print(f"  ❌ Column count MISMATCH:")
        for n, vintages in sorted(col_counts.items()):
            print(f"     {n} cols → {', '.join(vintages)}")

    return ok


def main() -> None:
    print(f"\nExtract directory: {EXTRACT_DIR}")

    all_ok = True
    for dtype in ("origination", "performance"):
        results = scan_parquets(dtype)
        if results:
            ok = report(dtype, results)
            all_ok = all_ok and ok

    print(f"\n{'═'*60}")
    if all_ok:
        print("  ✅ All checks passed.")
    else:
        print("  ❌ Some checks FAILED — see above.")
    print(f"{'═'*60}\n")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
