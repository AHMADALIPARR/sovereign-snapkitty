"""Test runner: no pytest dependency, plain asserts.

Discovers test_*.py in this directory, runs every test_* function, and
prints a per-file PASS/FAIL summary. Exit code 0 when all pass.
"""

import importlib.util
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    total = 0
    failed = 0
    for path in sorted(HERE.glob("test_*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        names = [n for n in dir(mod) if n.startswith("test_")]
        for name in names:
            total += 1
            try:
                getattr(mod, name)()
            except Exception:
                failed += 1
                print(f"FAIL {path.stem}::{name}")
                traceback.print_exc()
            else:
                print(f"PASS {path.stem}::{name}")
    print(f"\n{total - failed}/{total} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
