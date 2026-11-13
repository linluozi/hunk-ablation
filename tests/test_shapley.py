"""Tests for exact Shapley attribution and the strip/apply-cwd patch options."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hunkablation import attribute
from hunkablation.shapley import analyse, interactions, shapley

INTERACTION = Path(__file__).resolve().parent.parent / "examples" / "interaction"


def times_from_savings(savings: dict[int, float]) -> dict[int, float]:
    """A timing table consistent with the requested savings (offset is arbitrary)."""
    return {mask: 10.0 - value for mask, value in savings.items()}


def test_shapley_is_additive_when_hunks_are_independent():
    values = {0: 0.0, 1: 0.12, 2: 0.08, 3: 0.20}
    phi = shapley(values, 2)
    assert phi == pytest.approx([0.12, 0.08], abs=1e-12)
    res = analyse(times_from_savings(values), 2)
    assert res["loo_error_seconds"] == pytest.approx(0.0, abs=1e-12)
    assert res["efficiency_residual_seconds"] == pytest.approx(0.0, abs=1e-12)
    assert res["additivity_violated"] is False


def test_shapley_splits_a_perfect_synergy_and_flags_loo():
    # Each hunk alone saves nothing; together they save 0.2 s.  LOO reports
    # 0.2 s for *both* hunks (200% of the total); Shapley splits it 50/50.
    values = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.2}
    res = analyse(times_from_savings(values), 2)
    assert res["shapley_seconds"] == pytest.approx([0.1, 0.1], abs=1e-12)
    assert res["loo_seconds"] == pytest.approx([0.2, 0.2], abs=1e-12)
    assert res["loo_sum_seconds"] == pytest.approx(0.4, abs=1e-12)
    assert res["additivity_violated"] is True
    assert res["interaction"][0]["interaction_seconds"] == pytest.approx(0.2, abs=1e-12)


def test_negative_interaction_when_two_hunks_overlap():
    # Two optimisations of the same loop: each alone saves 0.1 s, together 0.15 s.
    values = {0: 0.0, 1: 0.1, 2: 0.1, 3: 0.15}
    pairs = interactions(values, 2)
    assert pairs[0]["interaction_seconds"] == pytest.approx(-0.05, abs=1e-12)
    res = analyse(times_from_savings(values), 2)
    assert res["shapley_seconds"] == pytest.approx([0.075, 0.075], abs=1e-12)
    assert res["additivity_violated"] is True


def make_repo(tmp_path: Path, source: Path, names: tuple[str, ...]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in names:
        shutil.copy(source / name, repo / name)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@e", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def test_shapley_end_to_end_on_the_synergy_demo(tmp_path):
    repo = make_repo(tmp_path, INTERACTION, ("work.py", "bench.py"))
    res = attribute(repo, (INTERACTION / "patch.diff").read_text(),
                    f"{sys.executable} bench.py", repeats=2, mode="shapley")
    assert res["conditions"] == 4
    assert res["total_savings_seconds"] == pytest.approx(0.2, abs=0.02)
    for hunk in res["hunks"]:
        assert hunk["shapley_seconds"] == pytest.approx(0.1, abs=0.02)
        assert hunk["savings_seconds"] == pytest.approx(0.2, abs=0.02)
        assert hunk["loo_shift_seconds"] == pytest.approx(-0.1, abs=0.03)
    assert res["shapley"]["additivity_violated"] is True
    assert res["shapley"]["efficiency_residual_seconds"] == pytest.approx(0.0, abs=0.02)
    assert subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                          cwd=repo, capture_output=True, text=True).stdout == ""


def test_strip_and_apply_cwd(tmp_path):
    """Patch paths carry two extra components and live under ``sub/``."""
    repo = tmp_path / "repo"
    (repo / "sub" / "pkg").mkdir(parents=True)
    (repo / "sub" / "pkg" / "mod.py").write_text("VALUE = 1\nOLD = 0\n")
    (repo / "patch.diff").write_text(
        "diff --git a/python/pkg/mod.py b/python/pkg/mod.py\n"
        "--- a/python/pkg/mod.py\n"
        "+++ b/python/pkg/mod.py\n"
        "@@ -1,2 +1,2 @@\n"
        " VALUE = 1\n"
        "-OLD = 0\n"
        "+OLD = 1\n")
    (repo / "bench.py").write_text(
        "import pathlib, time\n"
        "old = int(pathlib.Path('sub/pkg/mod.py').read_text().split('OLD = ')[1].split()[0])\n"
        "time.sleep(0.0 if old else 0.05)\n"
        "print('0.050000' if not old else '0.000000')\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@e", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)

    res = attribute(repo, (repo / "patch.diff").read_text(), f"{sys.executable} bench.py",
                    repeats=2, strip=2, apply_cwd="sub")
    assert res["total_hunks"] == 1
    assert res["total_savings_seconds"] == pytest.approx(0.05, abs=1e-6)
    assert res["hunks"][0]["share_pct"] == pytest.approx(100.0, abs=1e-6)
    assert (repo / "sub" / "pkg" / "mod.py").read_text() == "VALUE = 1\nOLD = 0\n"


def test_shapley_refuses_too_many_hunks(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    patch = []
    for i in range(7):
        (repo / f"f{i}.txt").write_text("x\n")
        patch.append(f"diff --git a/f{i}.txt b/f{i}.txt\n--- a/f{i}.txt\n"
                     f"+++ b/f{i}.txt\n@@ -1 +1 @@\n-x\n+y\n")
    (repo / "patch.diff").write_text("".join(patch))
    (repo / "bench.py").write_text("print('0.001000')\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@e", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)
    with pytest.raises(ValueError, match="at most 6"):
        attribute(repo, (repo / "patch.diff").read_text(), f"{sys.executable} bench.py",
                  repeats=1, mode="shapley")
