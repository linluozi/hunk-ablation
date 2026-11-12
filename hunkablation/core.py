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


def apply(repo: Path, patch: str, reverse: bool = False, check: bool = False) -> None:
    args = ["apply"]
    if reverse:
        args.append("-R")
    if check:
        args.append("--check")
    args.append("-")
    proc = _git(repo, args, stdin=patch)
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


def measure(cmd: str, cwd: Path, repeats: int = 5, timeout: float = 600.0) -> float:
    samples = []
    for _ in range(repeats):
        proc = subprocess.run(cmd, shell=True, cwd=cwd, text=True,
                              capture_output=True, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"benchmark failed ({proc.returncode}): {proc.stderr[-800:]}")
        samples.append(parse_seconds(proc.stdout))
    return statistics.median(samples)


def attribute(repo: Path, patch: str, bench_cmd: str, repeats: int = 5) -> dict:
    """Measure baseline, patched, and patched-minus-one-hunk for every hunk."""
    repo = Path(repo)
    hunks = split_patch(patch)
    if not is_clean(repo):
        raise DirtyTree("repository must be clean before ablation")
    apply(repo, patch, check=True)
    result: dict = {"repo": str(repo), "bench_cmd": bench_cmd, "repeats": repeats,
                    "hunks": [], "unrevertible": []}
    try:
        result["baseline_seconds"] = measure(bench_cmd, repo, repeats)
        apply(repo, patch)
        result["patched_seconds"] = measure(bench_cmd, repo, repeats)
        total = result["baseline_seconds"] - result["patched_seconds"]
        result["total_savings_seconds"] = total
        for hunk in hunks:
            try:
                apply(repo, hunk.as_patch(), reverse=True)
            except PatchError as exc:
                result["unrevertible"].append({"index": hunk.index, "error": str(exc)})
                continue
            try:
                t = measure(bench_cmd, repo, repeats)
            finally:
                apply(repo, hunk.as_patch())
            saving = t - result["patched_seconds"]
            row = hunk.to_dict() | {"reverted_seconds": t, "savings_seconds": saving,
                                    "share_pct": (100.0 * saving / total) if total else None}
            result["hunks"].append(row)
        explained = sum(h["savings_seconds"] for h in result["hunks"])
        result["explained_savings_seconds"] = explained
        result["explained_pct"] = (100.0 * explained / total) if total else None
        result["residual_seconds"] = total - explained
    finally:
        apply(repo, patch, reverse=True)
    if not is_clean(repo):
        raise RuntimeError("repository not restored to a clean state")
    return result
