"""Tests for hunkablation: patch splitting, measurement contract, attribution."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import hunkablation
from hunkablation import DirtyTree, PatchError, apply, attribute, is_clean, parse_seconds, split_patch

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo"


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("work.py", "bench.py"):
        shutil.copy(DEMO / name, repo / name)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@e", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def test_split_patch_two_hunks():
    hunks = split_patch((DEMO / "patch.diff").read_text())
    assert len(hunks) == 2
    assert [h.path for h in hunks] == ["work.py", "work.py"]
    assert hunks[0].header.startswith("@@ -2,7 +2,6 @@")
    assert any(line.strip() == "-    time.sleep(0.12)" for line in hunks[0].lines)
    assert hunks[1].header.startswith("@@ -15,7 +14,6 @@")


def test_split_patch_rejects_garbage():
    with pytest.raises(PatchError):
        split_patch("not a patch\n")


def test_parse_seconds_contract():
    assert parse_seconds("noise\n0.125\n") == 0.125
    assert parse_seconds('{"seconds": 0.5, "x": 1}') == 0.5
    with pytest.raises(ValueError):
        parse_seconds("")
    with pytest.raises(ValueError):
        parse_seconds("not-a-number")


def test_attribution_recovers_known_shares(tmp_path):
    repo = make_repo(tmp_path)
    res = attribute(repo, (DEMO / "patch.diff").read_text(), f"{sys.executable} bench.py",
                    repeats=3)
    total = res["total_savings_seconds"]
    assert 0.15 < total < 0.25, total
    shares = sorted(h["share_pct"] for h in res["hunks"])
    assert shares[0] == pytest.approx(40.0, abs=8.0), shares
    assert shares[1] == pytest.approx(60.0, abs=8.0), shares
    assert res["explained_pct"] == pytest.approx(100.0, abs=10.0)
    assert abs(res["residual_seconds"]) < 0.02
    assert is_clean(repo)
    assert (repo / "work.py").read_text().count("time.sleep") == 2  # restored


def test_dirty_tree_is_refused(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "work.py").write_text("changed\n")
    with pytest.raises(DirtyTree):
        attribute(repo, (DEMO / "patch.diff").read_text(), "true", repeats=1)


def test_bad_patch_refused_before_measuring(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(PatchError):
        apply(repo, "--- a/work.py\n+++ b/work.py\n@@ -1 +1 @@\n-x\n+y\n", check=True)


def test_cli_json_output(tmp_path):
    repo = make_repo(tmp_path)
    proc = subprocess.run([sys.executable, "-m", "hunkablation", "--repo", str(repo),
                           "--patch", str(DEMO / "patch.diff"), "--bench-cmd",
                           f"{sys.executable} bench.py", "--repeats", "3", "--json"],
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert len(payload["hunks"]) == 2 and payload["explained_pct"] > 80


def test_version():
    assert hunkablation.__version__ == "1.0.0"
