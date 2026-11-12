"""CLI: attribute a patch's measured saving to its individual hunks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import DirtyTree, PatchError, attribute


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hunkablation", description=__doc__)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--patch", required=True, type=Path)
    ap.add_argument("--bench-cmd", required=True,
                    help='command printing seconds on its last line, e.g. "python bench.py"')
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        res = attribute(args.repo, args.patch.read_text(), args.bench_cmd, args.repeats)
    except (PatchError, DirtyTree, RuntimeError, ValueError) as exc:
        sys.stderr.write(f"hunkablation: {exc}\n")
        return 2
    if args.json:
        print(json.dumps(res, indent=2))
        return 0
    print(f"baseline {res['baseline_seconds']:.6f}s  patched {res['patched_seconds']:.6f}s  "
          f"total saving {res['total_savings_seconds']:.6f}s")
    for h in res["hunks"]:
        share = "-" if h["share_pct"] is None else f"{h['share_pct']:6.1f}%"
        print(f"  hunk {h['index']} {h['path']:24} {h['header'][:44]:44} "
              f"saving {h['savings_seconds']:+.6f}s {share}")
    for u in res["unrevertible"]:
        print(f"  hunk {u['index']} UNREVERTIBLE: {u['error'][:120]}")
    explained = res["explained_pct"]
    print(f"explained {res['explained_savings_seconds']:.6f}s "
          f"({'-' if explained is None else f'{explained:.1f}%'})  "
          f"residual {res['residual_seconds']:+.6f}s")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
