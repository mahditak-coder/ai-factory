"""The five statuses are mutually exclusive; SKIP is never success."""
import unittest

from core import opstatus as ost


class StatusSemanticsTest(unittest.TestCase):
    def test_all_five_statuses_are_distinct(self):
        self.assertEqual(len(set(ost.STATUSES)), 5)
        self.assertNotIn(ost.SKIP, (ost.PASS, ost.FAIL))
        self.assertNotIn(ost.NOT_TESTED, (ost.PASS, ost.FAIL))

    def test_skip_never_yields_overall_pass(self):
        recs = [ost.check(ost.PASS, "build"), ost.check(ost.SKIP, "gh-probe")]
        self.assertEqual(ost.overall(recs), ost.NOT_TESTED)
        self.assertFalse(ost.summary(recs)["passed"])

    def test_not_tested_and_blocked_are_not_pass(self):
        for status in (ost.NOT_TESTED, ost.BLOCKED):
            with self.subTest(status=status):
                recs = [ost.check(ost.PASS, "a"), ost.check(status, "b")]
                self.assertNotEqual(ost.overall(recs), ost.PASS)

    def test_fail_wins_over_everything(self):
        recs = [ost.check(ost.FAIL, "a"), ost.check(ost.BLOCKED, "b"),
                ost.check(ost.SKIP, "c")]
        self.assertEqual(ost.overall(recs), ost.FAIL)

    def test_all_pass_is_the_only_pass(self):
        recs = [ost.check(ost.PASS, "a"), ost.check(ost.PASS, "b")]
        self.assertEqual(ost.overall(recs), ost.PASS)
        self.assertTrue(ost.summary(recs)["passed"])

    def test_optional_skip_does_not_block_pass(self):
        recs = [ost.check(ost.PASS, "a", required=True),
                ost.check(ost.SKIP, "external", required=False)]
        self.assertEqual(ost.overall(recs), ost.PASS)

    def test_empty_is_not_tested(self):
        self.assertEqual(ost.overall([]), ost.NOT_TESTED)

    def test_recorder_classifies_signals_and_legacy_skip_string(self):
        r = ost.Recorder()
        r.run("skip", lambda: ost.skip("no tool"))
        r.run("blocked", lambda: ost.blocked("policy"))
        r.run("untested", lambda: ost.not_tested("no evidence"))
        r.run("legacy", lambda: "SKIP: old harness")
        r.run("boom", lambda: 1 / 0)
        r.run("good", lambda: "real evidence")
        by = {c["name"]: c["status"] for c in r.records}
        self.assertEqual(by["skip"], ost.SKIP)
        self.assertEqual(by["blocked"], ost.BLOCKED)
        self.assertEqual(by["untested"], ost.NOT_TESTED)
        self.assertEqual(by["legacy"], ost.SKIP)
        self.assertEqual(by["boom"], ost.FAIL)
        self.assertEqual(by["good"], ost.PASS)
        self.assertNotEqual(r.finish()["status"], ost.PASS)


if __name__ == "__main__":
    unittest.main()
