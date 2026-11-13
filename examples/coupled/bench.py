"""Fixture benchmark for the coupled three-hunk patch.

Prints wall seconds on the last line.  ``lookup.py`` cannot be imported until
``table.py`` defines ``_TABLE``, so the subset with only the lookup hunk applied
raises ImportError; that is the coupling the grouping step must detect.
"""

import shutil
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
shutil.rmtree(Path(__file__).resolve().parent / "__pycache__", ignore_errors=True)

from run import main  # noqa: E402

t0 = time.perf_counter()
main()
print(f"{time.perf_counter() - t0:.6f}")
