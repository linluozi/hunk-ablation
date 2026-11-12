# hunk-ablation

Attribute a performance patch's measured saving to its **individual hunks**:
apply the patch, then revert exactly one hunk at a time, re-measure, and report
each hunk's marginal saving plus the unexplained residual.

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

`python -m pytest -q` → **8 passed**:

* patch splitting on the shipped two-hunk example, plus a rejection test for a
  non-patch;
* the measurement contract (plain float line, JSON payload, empty/garbage);
* end-to-end attribution on a freshly `git init`-ed copy of `examples/demo`:
  shares recovered at 60/40 within ±8 points, explained 100% ±10, residual
  < 0.02 s, tree restored;
* refuses a dirty tree (`DirtyTree`) and a non-applying patch (`PatchError`)
  before spending any measurement time;
* CLI JSON output, and full restoration of `work.py` to its two-sleep base.

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

## 本轮创新

**B 档**：把「逐 hunk 消融归因」从人工一次性脚本变成可复用工具——自动拆 hunk、
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
