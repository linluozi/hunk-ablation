# hunk-ablation

Attribute a performance patch's measured saving to its **individual hunks**:
apply the patch, then revert exactly one hunk at a time, re-measure, and report
each hunk's marginal saving plus the unexplained residual.  Version 1.1 adds
**exact Shapley attribution** (`--shapley`), which stays correct when hunks
overlap, and refuses to report shares for hunks that cannot be measured apart.

Most "X is 1.8× faster" claims come from a patch with three changes. This tool
answers the question a reviewer actually asks: *which of those three earned the
1.8×, and how much is left over?*

Upstream: none

```text
$ hunkablation --repo /tmp/demo --patch examples/demo/patch.diff \
               --bench-cmd "python3 bench.py" --repeats 5
baseline 0.200180s  patched 0.000000s  total saving 0.200170s
  hunk 0 work.py  @@ -2,7 +2,6 @@ import time   saving +0.120100s   60.0%
  hunk 1 work.py  @@ -15,7 +14,6 @@ def joiner(): saving +0.080100s  40.0%
explained 0.200200s (100.0%)  residual -0.000030s
```

The demo patch removes two `time.sleep` calls (0.12 s and 0.08 s) so the
expected attribution is known exactly: 60/40. The tool recovers it from
timings alone. The sleeps are a **deterministic arithmetic check for the tool**,
not a performance claim.

## Why Shapley (v1.1)

Leave-one-out attribution answers "what is lost if this hunk is removed from
the full patch?".  When two hunks overlap, that question double-counts: two
hunks that each unlock the same 0.2 s report `+0.2 s` **each**, i.e. 200% of
the total saving.  `--shapley` measures all `2**n` subsets and reports the
unique efficient attribution (`sum(parts) == total`), the per-pair second-order
interaction `v(N) - v(N\i) - v(N\j) + v(N\{i,j})`, and a verdict when the
largest interaction exceeds 5% of the total.

The shipped `examples/interaction/` fixture is a real, deterministic check of
that failure: `time.sleep(0.2)` runs unless *both* `FLAG_A` and `FLAG_B` are
set, so each hunk alone saves nothing and together they save 0.2 s.  Measured
by the tool (`--shapley`, 2 repeats per subset, ground truth known):

```text
LOO      +0.2001s / +0.2001s   (sum 200.1% of the total)
Shapley  +0.1000s / +0.1000s   (efficiency residual 0.000000s)
interaction +0.2001s           VERDICT: LOO shares are misleading
```

### What a real patch taught us: hunks that cannot run apart

The 4-hunk js-beautify tokenizer patch (from `linluozi/js-beautify-opt-lab`)
cannot be subset-attributed: hunk 1 uses `_EXTRA_WHITESPACE`, which hunk 0
defines, so every subset that reverts hunk 0 while keeping hunk 1 raises
`NameError`.  The tool now reports the failing subset and refuses instead of
timing a crashing program:

```text
$ hunkablation --repo <js-beautify checkout> --patch patch.diff --shapley \
               --strip 2 --apply-cwd src --bench-cmd "python3 bench_one.py"
hunkablation: subset 1 (hunks [0]) does not run: ... NameError: name '_EXTRA_WHITESPACE' is not defined.
Hunks that cannot run apart are not independently attributable; exclude them
with --only-hunks or group them.
```

LOO never sees this, because it only reverts one hunk from the fully patched
tree.  Subset attribution must check runnability first; grouping coupled hunks
into one unit is the next step and is **not** implemented in 1.1.0.

### Patch layouts

Real patches are not always written against the checkout root.  `--strip N`
passes `git apply -pN`, and `--apply-cwd DIR` prefixes the stripped path via
`git apply --directory=DIR`.  The latter is deliberate: `git apply -pN` run
*from* a subdirectory silently skips a patch whose path then does not exist
relative to the top level (verified with git 2.43, exit code 0, no change).
`--only-path` / `--only-hunks` narrow what is attributed; unselected hunks stay
applied and their saving lands in the residual.

## Contract

`--bench-cmd` must be a command run inside the repo that prints one number of
seconds as its last non-empty stdout line (or a JSON object with a `seconds`
key). The tool never inspects the patch: it applies `patch.diff`, measures, then
builds a one-hunk patch per hunk and applies it with `git apply -R`.

* `--repeats N` (default 5) takes the median of N runs per condition.
* The repository must have no modified tracked files before the run, and is
  restored with `git apply -R` in a `finally` block; the tool verifies the tree
  is clean again before returning.
* A hunk that cannot be reverted alone (overlapping context) is reported under
  `unrevertible` instead of silently skipped.
* `--shapley` needs a runnable program for every subset; a subset that crashes
  aborts the run with the subset and the stderr tail (no numbers are invented).

## Install

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[test]"
.venv/bin/python -m pytest -q          # 8 passed
```

Requires Python ≥ 3.10 (developed and tested on 3.12) and a git checkout (git must be on PATH). No third-party
runtime dependencies.

## Library use

```python
from hunkablation import attribute

res = attribute("/path/to/repo", open("patch.diff").read(),
                "python bench.py", repeats=5)
res["explained_pct"], res["residual_seconds"]
for h in res["hunks"]:
    print(h["index"], h["path"], h["header"], h["share_pct"])
```

## What the numbers mean

* `savings_seconds` for hunk *i* = `time(patched minus hunk i) - time(patched)`.
  It is the marginal cost of that hunk, measured in the presence of the others.
* `explained_pct` = Σ marginal savings / total saving. Values near 100% mean the
  hunks are close to additive on this workload; a large positive residual means
  interactions or an unmeasured change; a negative residual means the whole is
  less than the sum of its parts (typical for two optimizations of the same
  loop).
* Attribution is workload-specific. A hunk that shows 0% here can still matter
  on another input; the tool reports marginal contribution, not causality.

## Tested

`python -m pytest -q` → **14 passed**:

* patch splitting on the shipped two-hunk example, plus a rejection test for a
  non-patch;
* the measurement contract (plain float line, JSON payload, empty/garbage);
* end-to-end attribution on a freshly `git init`-ed copy of `examples/demo`:
  shares recovered at 60/40 within ±8 points, explained 100% ±10, residual
  < 0.02 s, tree restored;
* refuses a dirty tree (`DirtyTree`) and a non-applying patch (`PatchError`)
  before spending any measurement time;
* CLI JSON output, and full restoration of `work.py` to its two-sleep base;
* Shapley on known additive, perfectly synergistic and antagonistic two-hunk
  games (0.12/0.08, 0.1/0.1 with a +0.2 interaction, 0.075/0.075 with -0.05);
* the synergy fixture measured end to end: Shapley 0.1/0.1 while LOO reports
  0.2/0.2 and the verdict is `additivity_violated`;
* `--strip 2 --apply-cwd sub` on a patch whose paths carry two extra
  components and live under `sub/`, including a clean-tree check afterwards;
* the >6-hunk guard refuses before spending measurement time.

## Limits

* Reverting one hunk at a time assumes each hunk compiles and runs on its own;
  the tool reports unrevertible hunks but cannot make a broken half-revert work.
* Stale compiled artifacts invalidate the measurement. The demo deletes
  `__pycache__` before importing the patched module; **any** benchmark that
  imports patched code must do the same (a same-size, same-second edit can reuse
  a stale `.pyc` — this bit the demo during development and the fix is in
  `examples/demo/bench.py`).
* Noise floor: run with `--repeats` high enough that the median is stable; the
  tool reports raw seconds only and does not compute confidence intervals.

## 本轮创新（v1.1）

**B 档（v1.1 增补）**：把归因从留一法（LOO）推进到**精确 Shapley**（2^n 全子集），
并把「hunk 之间不可独立回退/不可独立运行」从静默错误变成显式拒绝：在真实 4-hunk
补丁上实测到 hunk 0/1 的结构耦合（`NameError: _EXTRA_WHITESPACE`），因此该补丁的
子集归因无定义，工具不产出数字。此前台账的归因只做单 hunk 回退，未检查子集可运行性，
也没有量化 LOO 在协同 hunk 上可报出 200% 的份额。

**B 档（v1.0）**：把「逐 hunk 消融归因」从人工一次性脚本变成可复用工具——自动拆 hunk、
单 hunk 反向应用、重测、给出每 hunk 边际节省与**未解释残差**，并在结束时校验工作树
已恢复。此前台账里的归因都是「调用点采样」或「最弱 2 文件门禁」，没有把
patch 的每个 hunk 单独回退后重测、再对总节省做闭合的工具，也没有把「残差」当作
结论文本的一部分。

## Environment

Linux 6.8, CPython 3.12.3, git 2.43, AMD EPYC 7K62 (4 vCPU). Timings above from
the shipped demo on this host; your numbers will differ, the shares should not by
much.

## License

MIT (see `LICENSE`). Original work.
