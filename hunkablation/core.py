"""Split a unified diff into hunks, apply subsets, and attribute measured savings.

The contract with the benchmark is deliberately narrow: the command must print
one number (seconds) as its last non-empty stdout line, or a JSON object with a
``seconds`` key. The tool re-runs it under four conditions - clean tree, fully
patched tree, and patched tree with exactly one hunk reverted - and reports each
hunk's marginal contribution to the total measured saving.
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


class PatchError(ValueError):
    pass


class DirtyTree(RuntimeError):
    pass


@dataclass
class Hunk:
    index: int
    path: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: list[str] = field(default_factory=list)

    def as_patch(self) -> str:
        body = "".join(self.lines)
        if body and not body.endswith("\n"):
            body += "\n"
        return (f"diff --git a/{self.path} b/{self.path}\n"
                f"--- a/{self.path}\n+++ b/{self.path}\n{self.header}\n{body}")

    def to_dict(self) -> dict:
        return {"index": self.index, "path": self.path, "header": self.header,
                "old": [self.old_start, self.old_count],
                "new": [self.new_start, self.new_count]}


def split_patch(text: str) -> list[Hunk]:
    """Return every hunk in ``text`` with the file path it belongs to."""
    hunks: list[Hunk] = []
    path = None
    cur: Hunk | None = None
    for line in text.splitlines(keepends=True):
        if line.startswith("+++ "):
            raw = line[4:].strip().split("\t")[0]
            path = raw[2:] if raw.startswith("b/") else raw
            continue
        if line.startswith("diff --git "):
            cur = None
            continue
        m = HUNK_RE.match(line.rstrip("\n"))
        if m:
            if path is None:
                raise PatchError("hunk before any '+++ b/<path>' header")
            cur = Hunk(len(hunks), path, int(m.group(1)), int(m.group(2) or 1),
                       int(m.group(3)), int(m.group(4) or 1), line.rstrip("\n"))
            hunks.append(cur)
            continue
        if cur is not None:
            if line.startswith(("+", "-", " ")) or line.startswith("\\"):
                cur.lines.append(line)
            elif line.strip() == "":
                cur.lines.append("\n")
                cur = None
            else:
                cur = None
    if not hunks:
        raise PatchError("no hunks found")
    return hunks


def _git(repo: Path, args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, input=stdin, text=True,
                          capture_output=True)


def apply(repo: Path, patch: str, reverse: bool = False, check: bool = False,
          strip: int = 1, cwd: str = ".") -> None:
    """Apply ``patch`` inside ``repo/cwd`` with ``git apply -p<strip>``.

    ``strip``/``cwd`` exist because real patches are written against a source
    tree whose layout differs from the checkout: js-beautify's patch names
    ``python/jsbeautifier/...`` while the checkout root is that same ``python/``
    directory, so it needs ``-p2`` from ``src/``.
    """
    args = ["apply", "--whitespace=nowarn"]
    if reverse:
        args.append("-R")
    if check:
        args.append("--check")
    if cwd not in (".", "", None):
        # Run at the repository root and prefix the stripped path instead of
        # changing directory: `git apply -pN` from a subdirectory silently
        # *skips* patches whose path then does not exist relative to the
        # top-level (verified with git 2.43).
        args.append(f"--directory={cwd}")
    args.append(f"-p{strip}")
    args.append("-")
    proc = _git(Path(repo), args, stdin=patch)
    if proc.returncode != 0:
        raise PatchError(f"git apply failed ({proc.returncode}): {proc.stderr.strip()}")


def is_clean(repo: Path) -> bool:
    return _git(repo, ["status", "--porcelain", "--untracked-files=no"]).stdout.strip() == ""


def parse_seconds(stdout: str) -> float:
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("benchmark command produced no output")
    last = lines[-1]
    if last.startswith("{"):
        payload = json.loads(last)
        if "seconds" not in payload:
            raise ValueError("JSON output lacks a 'seconds' key")
        return float(payload["seconds"])
    return float(last)


def measure_raw(cmd: str, cwd: Path, repeats: int = 5,
                timeout: float = 600.0) -> list[float]:
    samples: list[float] = []
    for _ in range(repeats):
        proc = subprocess.run(cmd, shell=True, cwd=cwd, text=True,
                              capture_output=True, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"benchmark failed ({proc.returncode}): {proc.stderr[-800:]}")
        samples.append(parse_seconds(proc.stdout))
    return samples


def measure(cmd: str, cwd: Path, repeats: int = 5, timeout: float = 600.0) -> float:
    return statistics.median(measure_raw(cmd, cwd, repeats, timeout))


def _set_subset(repo: Path, selected: list[Hunk], mask: int, current: int,
                strip: int, cwd: str) -> int:
    """Move the tree from subset ``current`` to subset ``mask``.

    Only the hunks that differ are touched, so a hunk that is already reverted
    is never reverted twice (that is what makes the old one-hunk loop safe but
    a naive all-subsets loop fail).
    """
    order = sorted(range(len(selected)),
                   key=lambda i: (selected[i].path, selected[i].new_start))
    for i in reversed(order):
        if current >> i & 1 and not mask >> i & 1:
            apply(repo, selected[i].as_patch(), reverse=True, strip=strip, cwd=cwd)
    for i in order:
        if mask >> i & 1 and not current >> i & 1:
            apply(repo, selected[i].as_patch(), strip=strip, cwd=cwd)
    return mask


def _measure_subset(repo: Path, selected: list[Hunk], mask: int, current: int,
                    bench_cmd: str, repeats: int, strip: int, cwd: str,
                    timeout: float) -> tuple[float, list[float], int]:
    current = _set_subset(repo, selected, mask, current, strip, cwd)
    raw = measure_raw(bench_cmd, repo, repeats, timeout)
    return statistics.median(raw), raw, current


def _ablate_loo(repo: Path, hunks: list[Hunk], bench_cmd: str, repeats: int,
                result: dict, strip: int, cwd: str, timeout: float,
                baseline: float, patched: float) -> None:
    total = baseline - patched
    for hunk in hunks:
        try:
            apply(repo, hunk.as_patch(), reverse=True, strip=strip, cwd=cwd)
        except PatchError as exc:
            result["unrevertible"].append({"index": hunk.index, "error": str(exc)})
            continue
        try:
            t = measure(bench_cmd, repo, repeats, timeout)
        except RuntimeError as exc:
            result["unrevertible"].append({
                "index": hunk.index,
                "error": f"the tree without this hunk does not run: {exc}"})
            continue
        finally:
            apply(repo, hunk.as_patch(), strip=strip, cwd=cwd)
        saving = t - patched
        result["hunks"].append(hunk.to_dict() | {
            "reverted_seconds": t, "savings_seconds": saving,
            "share_pct": (100.0 * saving / total) if total else None})
    explained = sum(h["savings_seconds"] for h in result["hunks"])
    result["explained_savings_seconds"] = explained
    result["explained_pct"] = (100.0 * explained / total) if total else None
    result["residual_seconds"] = total - explained


def _ablate_shapley(repo: Path, hunks: list[Hunk], bench_cmd: str, repeats: int,
                    result: dict, strip: int, cwd: str, timeout: float,
                    baseline: float, patched: float, threshold: float,
                    group: bool = True) -> None:
    from .shapley import analyse, derive_blocks

    n = len(hunks)
    full = (1 << n) - 1
    times: dict[int, float] = {}
    samples: dict[int, list[float]] = {}
    failing: dict[int, str] = {}
    current = full
    for mask in range(1 << n):
        try:
            current = _set_subset(repo, hunks, mask, current, strip, cwd)
        except PatchError as exc:
            failing[mask] = f"git apply failed: {exc}"
            _git(repo, ["checkout", "--", "."])
            current = 0
            continue
        try:
            raw = measure_raw(bench_cmd, repo, repeats, timeout)
        except RuntimeError as exc:
            failing[mask] = str(exc)
            continue
        times[mask] = statistics.median(raw)
        samples[mask] = raw
    if 0 not in times:
        raise RuntimeError("the clean tree does not run; there is no baseline to attribute")
    blocked = [{"mask": mask,
                "hunks": [hunks[i].index for i in range(n) if mask >> i & 1],
                "error": failing[mask]}
               for mask in sorted(failing)]
    if failing and not group:
        raise RuntimeError(
            "subsets do not run: "
            + "; ".join(f"{b['mask']} (hunks {b['hunks']}): {b['error'][:120]}"
                        for b in blocked)
            + ". Hunks that cannot run apart are not independently attributable; "
              "pass --only-hunks to narrow, or drop --no-group to let the tool "
              "group them into indivisible blocks.")
    blocks = derive_blocks(set(failing), n)
    if blocks is None:
        raise RuntimeError(
            "no sound hunk partition exists: the fully patched tree failed to run "
            f"({failing.get(full, 'unknown')[:160]})")
    block_of: dict[int, int] = {}
    for bi, block in enumerate(blocks):
        for i in block:
            block_of[i] = bi
    m = len(blocks)
    remap: dict[int, float] = {}
    for bm in range(1 << m):
        mask = 0
        for bi, block in enumerate(blocks):
            if bm >> bi & 1:
                for i in block:
                    mask |= 1 << i
        if mask not in times:
            raise RuntimeError(
                f"internal: block union {bm} maps to hunk mask {mask}, which did "
                "not run; refusing to attribute")
        remap[bm] = times[mask]
    # Re-measure the empty subset at the end: the gap is drift, not
    # attribution, and a reader is entitled to see it.
    drift_zero, _, current = _measure_subset(repo, hunks, 0, current, bench_cmd,
                                             repeats, strip, cwd, timeout)
    _set_subset(repo, hunks, full, current, strip, cwd)
    analysis = analyse(remap, m, threshold)
    result["baseline_seconds"] = times[0]
    result["patched_seconds"] = times[full]
    result["total_savings_seconds"] = analysis["total_savings_seconds"]
    result["drift_seconds"] = abs(times[0] - drift_zero)
    result["samples_seconds"] = {str(k): v for k, v in samples.items()}
    result["conditions"] = len(times)
    result["block_conditions"] = 1 << m
    result["grouped"] = len(blocks) < n
    result["blocks"] = [
        {"id": bi, "hunks": list(block),
         "paths": sorted({hunks[i].path for i in block}),
         "headers": [hunks[i].header for i in block],
         "shapley_seconds": analysis["shapley_seconds"][bi],
         "shapley_share_pct": (100.0 * analysis["shapley_seconds"][bi]
                               / analysis["total_savings_seconds"])
         if analysis["total_savings_seconds"] else None,
         "loo_seconds": analysis["loo_seconds"][bi],
         "share_pct": (100.0 * analysis["loo_seconds"][bi]
                       / analysis["total_savings_seconds"])
         if analysis["total_savings_seconds"] else None}
        for bi, block in enumerate(blocks)]
    result["failed_conditions"] = blocked
    for bi, block in enumerate(blocks):
        rep = hunks[block[0]]
        result["hunks"].append(rep.to_dict() | {
            "members": list(block),
            "savings_seconds": analysis["loo_seconds"][bi],
            "share_pct": result["blocks"][bi]["share_pct"],
            "shapley_seconds": analysis["shapley_seconds"][bi],
            "shapley_share_pct": result["blocks"][bi]["shapley_share_pct"],
            "loo_shift_seconds": (analysis["shapley_seconds"][bi]
                                  - analysis["loo_seconds"][bi]),
        })
    result["explained_savings_seconds"] = analysis["loo_sum_seconds"]
    result["explained_pct"] = (100.0 * analysis["loo_sum_seconds"]
                               / analysis["total_savings_seconds"]) \
        if analysis["total_savings_seconds"] else None
    result["residual_seconds"] = analysis["loo_error_seconds"]
    result["shapley"] = analysis


def attribute(repo: Path, patch: str, bench_cmd: str, repeats: int = 5, *,
              strip: int = 1, apply_cwd: str = ".", only_path: str | None = None,
              only_hunks: list[int] | None = None,
              mode: str = "loo", threshold: float = 0.05, group: bool = True,
              timeout: float = 600.0) -> dict:
    """Measure the patch's saving and attribute it to its hunks.

    ``mode="loo"`` measures baseline, patched, and patched-minus-one-hunk.
    ``mode="shapley"`` measures all ``2**n`` subsets and reports exact Shapley
    values plus the pairwise interaction that makes LOO shares misleading.
    ``strip``/``apply_cwd`` are passed to ``git apply``; ``only_path`` restricts
    attribution to hunks whose path contains that substring (the rest stay part
    of the measured patch and land in the residual).

    In Shapley mode, subsets whose benchmark crashes (a hunk that uses a name
    another hunk defines, for instance) are *not* timed.  ``group=True`` derives
    the finest sound partition of hunks into indivisible blocks and runs the
    Shapley game over blocks; ``group=False`` restores the old behaviour of
    aborting with the failing subset.
    """
    repo = Path(repo)
    if mode not in ("loo", "shapley"):
        raise ValueError(f"unknown mode {mode!r}")
    all_hunks = split_patch(patch)
    selected = [h for h in all_hunks
                if (only_path is None or only_path in h.path)
                and (only_hunks is None or h.index in only_hunks)]
    if not selected:
        raise PatchError(f"no hunk path contains {only_path!r}")
    if mode == "shapley" and len(selected) > 6:
        raise ValueError("shapley mode supports at most 6 selected hunks "
                         f"({len(selected)} selected); use --only-path to narrow")
    if not is_clean(repo):
        raise DirtyTree("repository must be clean before ablation")
    apply(repo, patch, check=True, strip=strip, cwd=apply_cwd)
    applied = False
    result: dict = {"repo": str(repo), "bench_cmd": bench_cmd, "repeats": repeats,
                    "mode": mode, "strip": strip, "apply_cwd": apply_cwd,
                    "selected_hunks": len(selected), "total_hunks": len(all_hunks),
                    "hunks": [], "unrevertible": []}
    try:
        baseline = measure(bench_cmd, repo, repeats, timeout)
        apply(repo, patch, strip=strip, cwd=apply_cwd)
        applied = True
        if _git(repo, ["diff", "--quiet"]).returncode == 0:
            raise PatchError(
                "the patch changed no tracked file: git apply skipped it. When "
                "--repo is a subdirectory of a git checkout, git prefixes the "
                "paths and silently skips them; pass the repository root and use "
                "--apply-cwd for the subdirectory.")
        patched = measure(bench_cmd, repo, repeats, timeout)
        result["baseline_seconds"] = baseline
        result["patched_seconds"] = patched
        result["total_savings_seconds"] = baseline - patched
        if mode == "loo":
            _ablate_loo(repo, selected, bench_cmd, repeats, result, strip,
                        apply_cwd, timeout, baseline, patched)
        else:
            _ablate_shapley(repo, selected, bench_cmd, repeats, result, strip,
                            apply_cwd, timeout, baseline, patched, threshold,
                            group)
    finally:
        if applied:
            try:
                apply(repo, patch, reverse=True, strip=strip, cwd=apply_cwd)
            except PatchError:
                # A measurement may have died halfway through a subset; reset
                # the tracked files rather than leaving a half-patched tree.
                _git(repo, ["checkout", "--", "."])
    if not is_clean(repo):
        raise RuntimeError("repository not restored to a clean state")
    return result
