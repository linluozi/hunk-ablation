"""Hunk-level ablation attribution for measured performance patches."""

from .core import (DirtyTree, Hunk, PatchError, apply, attribute, is_clean,
                   measure, parse_seconds, split_patch)

__all__ = ["DirtyTree", "Hunk", "PatchError", "apply", "attribute", "is_clean",
           "measure", "parse_seconds", "split_patch"]
__version__ = "1.0.0"
