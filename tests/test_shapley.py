"""Tests for exact Shapley attribution and the strip/apply-cwd patch options."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hunkablation import attribute, derive_blocks, is_clean, minimal_failing_sets
from hunkablation.shapley import analyse, interactions, shapley

INTERACTION = Path(__file__).resolve().parent.parent / "examples" / "interaction"
COUPLED = Path(__file__).resolve().parent.parent / "examples" / "coupled"


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


def test_minimal_failing_sets_keeps_only_the_smallest():
    # {hunk1} fails, and every superset of it also fails; only {hunk1} is minimal.
    assert minimal_failing_sets({0b010, 0b110, 0b111}) == [0b010]
    assert minimal_failing_sets(set()) == []
    assert minimal_failing_sets({0b01, 0b10}) == [0b01, 0b10]


def test_derive_blocks_merges_only_coupled_hunks():
    # nothing fails -> every hunk stays its own block
    assert derive_blocks(set(), 3) == [[0], [1], [2]]
    # hunk 1 uses a name hunk 0 defines: {1} and {1,2} fail, so 0 and 1 merge
    assert derive_blocks({0b010, 0b110}, 3) == [[0, 1], [2]]
    # a failing singleton merges with its smallest runnable superset
    assert derive_blocks({0b10}, 2) == [[0, 1]]
    # the fully patched tree fails -> nothing can be measured
    assert derive_blocks({0b111}, 3) is None


def make_coupled_repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path, COUPLED,
                     ("bench.py", "run.py", "lookup.py", "table.py", "tail.py"))


def test_grouped_shapley_end_to_end_on_the_coupled_fixture(tmp_path):
    repo = make_coupled_repo(tmp_path)
    res = attribute(repo, (COUPLED / "patch.diff").read_text(),
                    f"{sys.executable} bench.py", repeats=3, mode="shapley")
    assert res["grouped"] is True
    assert res["selected_hunks"] == 3
    assert [b["hunks"] for b in res["blocks"]] == [[0, 1], [2]]
    # six subsets ran (two crashed); the block game itself uses 2**2 = 4.
    assert res["conditions"] == 6
    assert res["block_conditions"] == 4
    assert [f["hunks"] for f in res["failed_conditions"]] == [[0], [0, 2]]
    assert res["total_savings_seconds"] == pytest.approx(0.24, abs=0.03)
    block0, block1 = res["blocks"]
    assert block0["shapley_seconds"] == pytest.approx(0.20, abs=0.03)
    assert block1["shapley_seconds"] == pytest.approx(0.04, abs=0.03)
    assert block0["shapley_share_pct"] == pytest.approx(83.3, abs=5.0)
    assert block1["shapley_share_pct"] == pytest.approx(16.7, abs=5.0)
    assert res["shapley"]["efficiency_residual_seconds"] == pytest.approx(0.0, abs=0.02)
    assert is_clean(repo)


def test_no_group_still_refuses_the_coupled_fixture(tmp_path):
    repo = make_coupled_repo(tmp_path)
    with pytest.raises(RuntimeError, match="do not run"):
        attribute(repo, (COUPLED / "patch.diff").read_text(),
                  f"{sys.executable} bench.py", repeats=2, mode="shapley",
                  group=False)
    assert is_clean(repo)


def test_cli_json_reports_blocks(tmp_path):
    repo = make_coupled_repo(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "hunkablation", "--repo", str(repo),
         "--patch", str(COUPLED / "patch.diff"), "--bench-cmd",
         f"{sys.executable} bench.py", "--repeats", "2", "--shapley", "--json"],
        capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["grouped"] is True
    assert [b["hunks"] for b in payload["blocks"]] == [[0, 1], [2]]
    assert payload["failed_conditions"]
