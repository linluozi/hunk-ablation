"""CLI: attribute a patch's measured saving to its individual hunks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import DirtyTree, PatchError, attribute


def _print_loo(res: dict) -> None:
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


def _print_shapley(res: dict) -> None:
    sh = res["shapley"]
    total = res["total_savings_seconds"]
    print(f"conditions {res['conditions']}  baseline {res['baseline_seconds']:.6f}s  "
          f"patched {res['patched_seconds']:.6f}s  total saving {total:.6f}s  "
          f"drift {res['drift_seconds']:.6f}s")
    print(f"  {'hunk':>4} {'path':24} {'LOO s':>10} {'LOO %':>7} "
          f"{'Shapley s':>10} {'Shapley %':>9} {'shift s':>9}")
    for h in res["hunks"]:
        loo_pct = "-" if h["share_pct"] is None else f"{h['share_pct']:.1f}"
        sha_pct = "-" if h["shapley_share_pct"] is None else f"{h['shapley_share_pct']:.1f}"
        print(f"  {h['index']:>4} {h['path']:24} {h['savings_seconds']:+10.6f} "
              f"{loo_pct:>7} {h['shapley_seconds']:+10.6f} {sha_pct:>9} "
              f"{h['loo_shift_seconds']:+9.6f}")
    print(f"efficiency residual {sh['efficiency_residual_seconds']:+.6f}s "
          f"(Shapley parts must sum to the total)")
    pct = (100.0 * sh["loo_sum_seconds"] / total) if total else float("nan")
    print(f"LOO sum {sh['loo_sum_seconds']:.6f}s = {pct:.1f}% of total  "
          f"error {sh['loo_error_seconds']:+.6f}s")
    for p in sh["interaction"]:
        if abs(p["interaction_pct_of_total"]) >= 1.0:
            print(f"  interaction hunk {p['i']} x {p['j']}: "
                  f"{p['interaction_seconds']:+.6f}s "
                  f"({p['interaction_pct_of_total']:+.1f}% of total)")
    if sh["additivity_violated"]:
        print(f"VERDICT: LOO shares are misleading - max |interaction| "
              f"{sh['max_abs_interaction_seconds']:.6f}s exceeds "
              f"{sh['threshold'] * 100:.0f}% of the total saving; report Shapley values.")
    else:
        print("VERDICT: hunks are additive within the threshold; LOO shares are usable.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hunkablation", description=__doc__)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--patch", required=True, type=Path)
    ap.add_argument("--bench-cmd", required=True,
                    help='command printing seconds on its last line, e.g. "python bench.py"')
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--shapley", action="store_true",
                    help="measure all 2^n subsets and report exact Shapley values")
    ap.add_argument("--only-path", default=None,
                    help="attribute only hunks whose path contains this substring")
    ap.add_argument("--only-hunks", default=None,
                    help="comma-separated hunk indices to attribute (default: all)")
    ap.add_argument("--strip", type=int, default=1,
                    help="git apply -p level (default 1)")
    ap.add_argument("--apply-cwd", default=".",
                    help="subdirectory of --repo where the patch applies")
    ap.add_argument("--threshold", type=float, default=0.05,
                    help="max |interaction| / total saving before LOO is called misleading")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        res = attribute(args.repo, args.patch.read_text(), args.bench_cmd, args.repeats,
                        strip=args.strip, apply_cwd=args.apply_cwd,
                        only_path=args.only_path,
                        only_hunks=([int(x) for x in args.only_hunks.split(",")]
                                    if args.only_hunks else None),
                        mode="shapley" if args.shapley else "loo",
                        threshold=args.threshold)
    except (PatchError, DirtyTree, RuntimeError, ValueError) as exc:
        sys.stderr.write(f"hunkablation: {exc}\n")
        return 2
    if args.json:
        print(json.dumps(res, indent=2))
        return 0
    if args.shapley:
        _print_shapley(res)
    else:
        _print_loo(res)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
