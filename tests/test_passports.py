"""Паспорта: слияние слотов и этапы сторис."""

from __future__ import annotations

import unittest

from course.passports import filled_count, merge_slots, missing_slots, slots_to_markdown
from course.stories_cycle import normalize_stage, stage_title, writer_stories_rules


class PassportSlotsTests(unittest.TestCase):
    def test_merge_keeps_old_and_fills_new(self):
        cur = merge_slots("expert", {}, {"who": "психолог", "tone": ""})
        nxt = merge_slots("expert", cur, {"tone": "тепло и прямо"})
        self.assertEqual(nxt["who"], "психолог")
        self.assertEqual(nxt["tone"], "тепло и прямо")
        n, total = filled_count(nxt)
        self.assertGreaterEqual(total, 4)
        self.assertGreaterEqual(n, 2)
        miss = missing_slots("expert", nxt)
        self.assertTrue(any(x.startswith("taboo") for x in miss))

    def test_markdown_contains_title(self):
        md = slots_to_markdown("launch", {"dates": "окно с 1 октября", "offer": ""})
        self.assertIn("Паспорт запуска", md)
        self.assertIn("окно с 1 октября", md)


class StoriesCycleTests(unittest.TestCase):
    def test_unknown_falls_back_to_warmup(self):
        self.assertEqual(normalize_stage("???"), "warmup")
        self.assertIn("прогрев", stage_title("warmup").lower())

    def test_launch_rules_mention_offer(self):
        text = writer_stories_rules("launch")
        self.assertIn("оффер", text.lower())
        self.assertIn("кадр", text.lower())


if __name__ == "__main__":
    unittest.main()
