"""Exact Shapley attribution for a small set of patch hunks.

Leave-one-out (LOO) attribution answers "what is lost if this hunk is removed
from the full patch?".  That is a *marginal* cost in one particular context and
it double-counts when hunks overlap: two hunks that each unlock the same saving
report 100% each, so the reported shares sum to 200%.  The Shapley value is the
unique attribution that is efficient (the parts sum to the whole) and symmetric,
computed here exactly by enumerating all ``2**n`` subsets (``n <= 6`` keeps the
measurement budget bounded).

Both numbers are useful; what is not useful is publishing the LOO shares when
the second-order interaction is large.  ``analyse`` returns the interaction
matrix so the caller can refuse.
"""

from __future__ import annotations

from itertools import combinations
from math import factorial


def saving_values(times: dict[int, float]) -> dict[int, float]:
    """Turn measured timings into savings relative to the empty subset."""
    base = times[0]
    return {mask: base - t for mask, t in times.items()}


def shapley(values: dict[int, float], n: int) -> list[float]:
    """Exact Shapley values of the saving game, in hunk order.

    ``values[mask]`` is the measured saving with exactly ``mask`` applied.
    Only ``2**n`` lookups are needed; the weight of a coalition of size ``s``
    is ``s! (n-s-1)! / n!``.
    """
    phi = [0.0] * n
    for i in range(n):
        bit = 1 << i
        for mask in range(1 << n):
            if mask & bit:
                continue
            s = mask.bit_count()
            weight = factorial(s) * factorial(n - s - 1) / factorial(n)
            phi[i] += weight * (values[mask | bit] - values[mask])
    return phi


def interactions(values: dict[int, float], n: int) -> list[dict]:
    """Second-order interaction for every pair: positive = synergy.

    ``d(i,j) = v(N) - v(N\\i) - v(N\\j) + v(N\\{i,j})``.  It is zero exactly when
    the two hunks are additive, and it is the amount by which the sum of the
    two leave-one-out marginals overstates the full patch's saving.
    """
    full = (1 << n) - 1
    out = []
    for i, j in combinations(range(n), 2):
        both_off = full & ~(1 << i) & ~(1 << j)
        d = (values[full] - values[full & ~(1 << i)]
             - values[full & ~(1 << j)] + values[both_off])
        out.append({"i": i, "j": j, "interaction_seconds": d})
    return out


def analyse(times: dict[int, float], n: int, threshold: float = 0.05) -> dict:
    """Shapley values, LOO marginals, and the additivity verdict.

    ``threshold`` is the interaction size (as a fraction of the full saving)
    above which LOO shares are declared misleading.
    """
    values = saving_values(times)
    full = (1 << n) - 1
    total = values[full]
    phi = shapley(values, n)
    loo = [values[full] - values[full & ~(1 << i)] for i in range(n)]
    pairs = interactions(values, n)
    scale = abs(total) if total else 1.0
    for pair in pairs:
        pair["interaction_pct_of_total"] = 100.0 * pair["interaction_seconds"] / scale
    worst = max((abs(p["interaction_seconds"]) for p in pairs), default=0.0)
    return {
        "shapley_seconds": phi,
        "loo_seconds": loo,
        "total_savings_seconds": total,
        "efficiency_residual_seconds": sum(phi) - total,
        "loo_sum_seconds": sum(loo),
        "loo_error_seconds": sum(loo) - total,
        "interaction": pairs,
        "max_abs_interaction_seconds": worst,
        "additivity_violated": worst > threshold * scale,
        "threshold": threshold,
    }
