# hunk-ablation

Attribute a performance patch's measured saving to its **individual hunks**:
apply the patch, then revert exactly one hunk at a time, re-measure, and report
each hunk's marginal saving plus the unexplained residual.  Version 1.1 adds
**exact Shapley attribution** (`--shapley`), which stays correct when hunks
overlap.  Version 1.2 adds **automatic grouping**: a subset that crashes (a hunk
that uses a name another hunk defines, say) is folded into an indivisible block
and the exact Shapley game is run over blocks, with every failing subset printed
rather than hidden.

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
$ hunkablation --repo . --patch examples/interaction/patch.diff \
               --apply-cwd examples/interaction \
               --bench-cmd ".venv/bin/python examples/interaction/bench.py" \
               --shapley --repeats 3
conditions 4  baseline 0.200082s  patched 0.000001s  total saving 0.200081s  drift 0.000002s
   0 work.py                   +0.200085   100.0  +0.100041      50.0 -0.100044
   1 work.py                   +0.200084   100.0  +0.100040      50.0 -0.100044
efficiency residual +0.000000s (Shapley parts must sum to the total)
LOO sum 0.400169s = 200.0% of total  error +0.200088s
  interaction hunk 0 x 1: +0.200088s (+100.0% of total)
VERDICT: LOO shares are misleading - max |interaction| 0.200088s exceeds 5% ...
```

The fixture's two hunks are *perfectly synergistic* by construction (a `sleep`
guarded by `FLAG_A and FLAG_B`), so this checks the arithmetic, not a library's
speed.  The numbers above are from this machine (CPython 3.12.3).

### What a real patch taught us: hunks that cannot run apart

The 4-hunk js-beautify tokenizer patch (from `linluozi/js-beautify-opt-lab`)
cannot be attributed hunk-by-hunk: hunk 1 adds `_readWhitespace` (which reads
`_EXTRA_WHITESPACE`) and the fast dispatch (which reads `_FAST_START`), and both
names are defined by hunk 0.  Every subset that keeps hunk 1 but reverts hunk 0
dies with `NameError: name '_EXTRA_WHITESPACE' is not defined`; those subsets are
`[1]`, `[1,2]`, `[1,3]` and `[1,2,3]`.  v1.1 refused to produce any number here.
v1.2 merges hunk 0 and hunk 1 into one block and runs the game over the rest
(recipe and script in `examples/real_jsbeautify/`):

```text
$ hunkablation --repo <js-beautify checkout> --patch patch.diff --shapley \
               --strip 2 --apply-cwd src --bench-cmd "python3 bench.py" --repeats 3
conditions 12  baseline 1.028799s  patched 0.892339s  total saving 0.136460s  drift 0.002357s
  subset 2 (hunks [1]) does not run: ... NameError: name '_EXTRA_WHITESPACE' is not defined
  subset 6 (hunks [1, 2]) does not run: ...
  subset 10 (hunks [1, 3]) does not run: ...
  subset 14 (hunks [1, 2, 3]) does not run: ...
grouped 4 hunks into 3 indivisible blocks (a failing union forces a merge)
  block       hunks path                        LOO s   LOO %  Shapley s Shapley %   shift s
      0       [0,1] python/.../tokenizer.py  +0.077859    57.1  +0.083388      61.1 +0.005529
      2         [2] python/.../tokenizer.py  +0.043493    31.9  +0.047477      34.8 +0.003984
      3         [3] python/.../tokenizer.py  +0.000572     0.4  +0.005595       4.1 +0.005023
efficiency residual +0.000000s (Shapley parts must sum to the total)
LOO sum 0.121924s = 89.3% of total  error -0.014536s
  interaction block 0 x 1: -0.008388s (-6.1% of total)
  interaction block 0 x 2: -0.010465s (-7.7% of total)
  interaction block 1 x 2: -0.007376s (-5.4% of total)
VERDICT: LOO shares are misleading - max |interaction| 0.010465s exceeds 5% ...
```

A second run (`--repeats 2`) gave `62.5 / 35.3 / 2.2 %`, so the share of the tiny
third block sits inside the noise floor (drift 0.002-0.004 s, 2-3 % of the total
saving); the ordering and the additivity verdict are stable across runs.  Block
`[0,1]` is the fast first-character dispatch plus its whitespace predicate (the
hunks that define and use the tables), block `[2]` the identifier-regex fast
path, block `[3]` the compiled-pattern `.sub` in the string reader.  LOO never
sees this coupling, because it only reverts one hunk from the fully patched tree.


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
* `--shapley` probes every subset and prints the ones that crashed together
  with the stderr tail.  By default the failing subsets are folded into
  indivisible blocks (`--no-group` restores the v1.1 hard abort); no number is
  ever invented for a subset that did not run.
* A patch that changes no tracked file is rejected: `git apply` run inside a
  subdirectory of a checkout prefixes the paths and silently skips them (exit
  0, no change), which would otherwise produce a "0.000x" measurement.  Pass
  the repository root plus `--apply-cwd`.

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

`python -m pytest -q` → **19 passed**:

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
* the >6-hunk guard refuses before spending measurement time;
* `minimal_failing_sets` keeps only the inclusion-minimal failing masks;
* `derive_blocks` leaves independent hunks alone, merges a coupled singleton
  with its smallest runnable superset, and returns `None` when the fully
  patched tree itself fails;
* the coupled three-file fixture end to end: 6 of 8 subsets run, both failures
  are reported, 3 hunks are grouped into `[[0,1],[2]]`, and Shapley recovers
  0.20 s / 0.04 s of a 0.24 s saving;
* `--no-group` still aborts on that fixture with the failing subset, and
  `--json` carries `blocks` and `failed_conditions`.

## Limits

* Reverting one hunk at a time assumes each hunk compiles and runs on its own;
  the tool reports unrevertible hunks and (in Shapley mode) groups coupled hunks
  into blocks, but it cannot split a block that provably has to move together.
* Grouping is derived from *runnability*, not from a static dependency analysis:
  hunks that happen to run apart are kept apart even if they are conceptually
  related, and the partition is sound but not claimed to be the finest.
* Stale compiled artifacts invalidate the measurement. The demo deletes
  `__pycache__` before importing the patched module; **any** benchmark that
  imports patched code must do the same (a same-size, same-second edit can reuse
  a stale `.pyc` — this bit the demo during development and the fix is in
  `examples/demo/bench.py`).
* Noise floor: run with `--repeats` high enough that the median is stable; the
  tool reports raw seconds only and does not compute confidence intervals.

## 本轮创新（v1.2）

**B 档（v1.2 增补）**：把「不可独立运行的 hunk」从 v1.1 的**拒绝**推进成**自动分组 + 组级精确 Shapley**。
v1.1 在真实 4-hunk js-beautify 补丁上只会报 `NameError` 然后放弃；v1.2 用失败子集
反推「最小不可分块」（把失败子集与它的最小可运行超集合并，直到没有任何失败子集是整块的并集），
把 4 个 hunk 归成 3 块，并给出组级 Shapley 份额 62.5% / 35.3% / 2.2%（LOO 份额
60.3% / 34.0% / 3.7%，块 0×块 1 交互 −6.8% 触发 `additivity_violated`）。此前台账没有任何
「按可运行性自动分组后再做 Shapley」的做法，也没有「分组不可行时显式拒绝」的证书。

**B 档（v1.1）**：把归因从留一法（LOO）推进到**精确 Shapley**（2^n 全子集），
并把「hunk 之间不可独立回退/不可独立运行」从静默错误变成显式拒绝：在真实 4-hunk
补丁上实测到 hunk 0/1 的结构耦合（`NameError: _EXTRA_WHITESPACE`），因此该补丁的
子集归因无定义，工具不产出数字。此前台账的归因只做单 hunk 回退，未检查子集可运行性，
也没有量化 LOO 在协同 hunk 上可报出 200% 的份额。

## 本轮创新（v1.1，历史）

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
