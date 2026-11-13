"""Fixture benchmark for the real 4-hunk js-beautify tokenizer patch.

Prints wall seconds on the last line.  Reads a generated ~250 KB JavaScript
document and beautifies it with the package under ``src/``.
"""

import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, "src")

import jsbeautifier  # noqa: E402

DOC = ("var a = 1; function f(x) { return x + 1; } /* c */\n"
       "const s = 'a\\tb'; // trailing\n") * 3000

t0 = time.perf_counter()
jsbeautifier.beautify(DOC)
print(f"{time.perf_counter() - t0:.6f}")
