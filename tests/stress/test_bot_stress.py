"""Stress suite entry point for unittest / pytest. Opt-in, because it is meant to FAIL on branches that
do not have the answer-quality fixes yet:

    BOT_STRESS=1 python -m unittest tests.stress.test_bot_stress -v
    BOT_STRESS=1 python -m pytest tests/stress -m bot_stress          # if pytest is installed
    BOT_STRESS=1 BOT_STRESS_ONLY=budget python -m unittest ...         # ids / categories containing "budget"

Without BOT_STRESS=1 every test is skipped, so the normal test run is unaffected. For a table instead of
test output use ``python -m tests.stress.runner``. Fully offline: see tests/stress/harness.py.
"""

import os
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tests.stress.runner import load_cases, run_case  # noqa: E402

try:  # the marker is only meaningful under pytest; unittest ignores it
    import pytest

    pytestmark = pytest.mark.bot_stress
except ImportError:
    pass

ENABLED = os.environ.get("BOT_STRESS") == "1"
ONLY = os.environ.get("BOT_STRESS_ONLY", "")


@unittest.skipUnless(ENABLED, "set BOT_STRESS=1 to run the bot stress suite")
class BotStressCases(unittest.TestCase):
    def test_cases(self):
        for case in load_cases():
            if ONLY and ONLY not in case["id"] and ONLY not in case["category"]:
                continue
            with self.subTest(case=case["id"], category=case["category"]):
                result = run_case(case)
                problems = [f"{t['say']!r}: {p}" for t in result["turns"] for p in t["problems"]]
                self.assertFalse(problems, f"{case['id']} ({case['title']})\n  " + "\n  ".join(problems))


if __name__ == "__main__":
    unittest.main()
