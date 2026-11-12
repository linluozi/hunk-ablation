"""Fixture benchmark: prints wall seconds on the last line.

The patched module is tiny, so a stale __pycache__ would silently benchmark the
previous revision (same byte size, same-second mtime). Drop it before importing.
"""

import shutil
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
shutil.rmtree(Path(__file__).resolve().parent / "__pycache__", ignore_errors=True)

from work import run  # noqa: E402

t0 = time.perf_counter()
run()
print(f"{time.perf_counter() - t0:.6f}")
