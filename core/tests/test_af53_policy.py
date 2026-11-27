"""AF-53 — fewer needless questions, real high-risk confirmations kept (UNIT)."""
import unittest
from core import agent_policy


class AskPolicyTest(unittest.TestCase):
    def test_mention_is_not_an_action(self):
        for goal in ("بررسی امنیتی کد را انجام بده و تست کن",
                     "مقدار token را در تنظیمات بررسی کن",
                     "یک فرم با پسورد بساز"):
            self.assertEqual(agent_policy.assess(goal)["decision"],
                             agent_policy.PROCEED, goal)

    def test_real_high_risk_actions_still_confirm(self):
        for goal in ("push to github", "deploy the product",
                     "فایل را حذف کن", "انتشار بده"):
            r = agent_policy.assess(goal)
            self.assertEqual(r["decision"], agent_policy.CONFIRM, goal)
            self.assertIn("پیشنمایش", r["options"][0])

    def test_recommended_option_is_first(self):
        r = agent_policy.assess("push to github")
        self.assertEqual(r["options"][0], "فقط پیشنمایش")


if __name__ == "__main__":
    unittest.main()
