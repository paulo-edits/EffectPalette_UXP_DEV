"""Runs the companion's off-host unit tests (no Premiere, no real plugin).

    python companion/tests/run_tests.py            # from the repo root
    npm run test:companion                          # same thing

These tests exercise the companion-side transport server against an in-process WebSocket client,
the catalog loader/search index against temporary files, and the pure helpers. Nothing here
proves a Premiere behavior - that still needs a real host test (see CLAUDE.md).
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPANION = HERE.parent

# Qt must not need a display (or the user's real settings/telemetry) for these tests.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile

_REPORT_TMP = tempfile.mkdtemp(prefix="fxpalette-tests-")
os.environ.setdefault("FX_PALETTE_REPORT_DIR", _REPORT_TMP)

if str(COMPANION) not in sys.path:
    sys.path.insert(0, str(COMPANION))


def main() -> int:
    suite = unittest.defaultTestLoader.discover(str(HERE), pattern="test_*.py", top_level_dir=str(HERE))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
