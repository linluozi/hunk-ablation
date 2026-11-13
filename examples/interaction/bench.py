"""Fixture benchmark for the synergistic two-hunk patch.

Prints wall seconds on the last line.  Sleeps 0.2 s unless both flags are on.
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
