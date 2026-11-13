# Real 4-hunk patch: js-beautify tokenizer

This example runs `hunkablation --shapley` on a **real** four-hunk performance
patch that v1.1 could not attribute at all: hunk 1 uses `_EXTRA_WHITESPACE` and
`_FAST_START`, both defined by hunk 0, so the subsets `[1]`, `[1,2]`, `[1,3]` and
`[1,2,3]` raise `NameError`.  v1.2 groups hunks 0 and 1 into one indivisible
block and attributes the patch over three blocks.

The patch and the patched package ship in
[`linluozi/js-beautify-opt-lab`](https://github.com/linluozi/js-beautify-opt-lab)
(the upstream library is [beautifier/js-beautify](https://github.com/beautifier/js-beautify),
MIT; the only change there is the tokenizer fast path measured in that repo).

## Reproduce (network: clone over SSH as usual)

```bash
# 1. patched source tree + the 4-hunk patch
git clone git@github-linluozi:linluozi/js-beautify-opt-lab.git jsb-lab
cd jsb-lab

# 2. turn it into a clean BASELINE tree so the patch can be applied forward
git apply -R -p2 --directory=src patch.diff
git -c user.email=t@e -c user.name=t commit -qam baseline

# 3. this directory's bench script (imports the package from src/)
cp ../bench.py bench.py

# 4. attribute the patch
python -m hunkablation --repo "$PWD" --patch patch.diff --shapley \
       --strip 2 --apply-cwd src --bench-cmd "python3 bench.py" --repeats 3
```

`--strip 2` removes the `a/` and `python/` components of the patch paths, and
`--apply-cwd src` prefixes the remainder, so `a/python/jsbeautifier/...` lands on
`src/jsbeautifier/...`.

## Recorded output (this machine, CPython 3.12.3)

```text
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

A second run with `--repeats 2` gave `62.5 / 35.3 / 2.2 %`.  The tiny third block
is inside the noise floor; the ordering and the additivity verdict are not.
