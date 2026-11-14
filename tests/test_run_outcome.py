"""A run stopped only by the daily page budget, with no errors, is healthy; real partial runs are not."""
import json
import unittest

from database.run_outcome import classify, exit_code, stopped_reason


class RunOutcomeTests(unittest.TestCase):
    def test_budget_limited_is_distinguished_from_real_partial_failures(self):
        budget = {"status": "partial", "errors": 0, "rejected": 0, "stopped_reason": "discovery_budget_incomplete",
                  "observations": 90}
        self.assertEqual("budget_limited", classify(budget))
        self.assertEqual(0, exit_code(budget))
        for broken in ({**budget, "errors": 1}, {**budget, "rejected": 2},
                       {**budget, "stopped_reason": "WebsiteReadError"}, {**budget, "stopped_reason": None}):
            self.assertEqual("partial", classify(broken), broken)
            self.assertEqual(1, exit_code(broken))

    def test_other_outcomes_keep_their_exit_codes(self):
        self.assertEqual(0, exit_code({"status": "completed", "errors": 0}))
        for status in ("failed", "paused", "budget_exhausted"):
            self.assertEqual(1, exit_code({"status": status, "errors": 0}), status)
        self.assertEqual("partial", classify({"status": "completed", "errors": 3}))
        self.assertEqual("none", classify(None))

    def test_stored_rows_are_read_from_the_report_json(self):
        row = {"status": "partial", "errors": 0,
               "report": json.dumps({"stopped_reason": "discovery_budget_incomplete", "rejected": 0})}
        self.assertEqual("budget_limited", classify(row))
        row["report"] = json.dumps({"stopped_reason": "discovery_budget_incomplete", "rejected": 1})
        self.assertEqual("partial", classify(row))
        self.assertIsNone(stopped_reason("not json"))
        self.assertEqual("partial", classify({"status": "partial", "errors": 0, "report": "{broken"}))


if __name__ == "__main__":
    unittest.main()
