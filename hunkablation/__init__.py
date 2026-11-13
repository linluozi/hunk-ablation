"""Hunk-level ablation attribution for measured performance patches."""

from .core import (DirtyTree, Hunk, PatchError, apply, attribute, is_clean,
                   measure, parse_seconds, split_patch)
from .shapley import (analyse, derive_blocks, derive_blocks_exact, interactions,
                      minimal_failing_sets, saving_values, shapley)

__all__ = ["DirtyTree", "Hunk", "PatchError", "analyse", "apply", "attribute",
           "derive_blocks", "derive_blocks_exact", "interactions", "is_clean", "measure",
           "minimal_failing_sets", "parse_seconds", "saving_values", "shapley",
           "split_patch"]
__version__ = "1.3.0"
