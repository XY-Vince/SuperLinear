#!/usr/bin/env python3
"""
Unit Tests for Daily Briefing Generator (Markdown, JSON, CSV)
"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from briefing import generate_briefing, render_briefing_markdown
from event_store import EventStore


class TestBriefingGenerator(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp_dir.name) / "state"
        self.out_dir = Path(self.tmp_dir.name) / "reports"
        self.store = EventStore(state_dir=str(self.state_dir))

        # Add sample events
        self.store.upsert_event({
            "id": "ev:01",
            "title": "Welcome Reception with Pizza",
            "starts_at": "2026-09-27T17:00:00-04:00",
            "ends_at": "2026-09-27T19:00:00-04:00",
            "location": "HQ 131",
            "host": "Judaic Studies",
            "food_status": "provided",
            "food_cost": "free",
            "admission_cost": "free",
            "confidence": "confirmed_free",
            "food_signals": ["pizza", "reception"],
            "rsvp_status": "Open",
            "participation_note": "Free pizza and drinks"
        })

        self.store.upsert_event({
            "id": "ev:02",
            "title": "Annual Gala Dinner",
            "starts_at": "2026-09-27T19:30:00-04:00",
            "location": "Woolsey Hall",
            "host": "Yale Club",
            "food_status": "provided",
            "food_cost": "paid",
            "admission_cost": "paid ($35.00)",
            "confidence": "needs_verification"
        })

        self.store.upsert_event({
            "id": "ev:03",
            "title": "Yale Ventures Founders Office Hours",
            "starts_at": "2026-09-27T14:00:00-04:00",
            "location": "100 College St",
            "host": "Yale Ventures",
            "food_status": "unclear",
            "food_cost": "unknown",
            "admission_cost": "free",
            "user_requested": 1,
            "record_kind": "user_interest",
            "rsvp_status": "报名截止 9/26 23:59"
        })

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_briefing_outputs_created(self):
        res = generate_briefing(
            state_dir=str(self.state_dir),
            target_date_str="2026-09-27",
            out_dir=str(self.out_dir)
        )
        self.assertEqual(res["meta"]["agenda_count"], 3)
        self.assertEqual(res["meta"]["confirmed_free_food_count"], 1)
        self.assertEqual(res["meta"]["explicit_user_interests"], 1)

        # Check output files
        self.assertTrue((self.out_dir / "daily-briefing.md").exists())
        self.assertTrue((self.out_dir / "daily-briefing.json").exists())
        self.assertTrue((self.out_dir / "daily-briefing.csv").exists())

        md_text = (self.out_dir / "daily-briefing.md").read_text(encoding="utf-8")
        self.assertIn("🎯 优先行动与报名提示", md_text)
        self.assertIn("🍔 明确免费餐饮日程", md_text)
        self.assertIn("💡 用户关注与精选创业社群活动", md_text)
        self.assertIn("Welcome Reception with Pizza", md_text)
        self.assertIn("Yale Ventures Founders Office Hours", md_text)


if __name__ == "__main__":
    unittest.main()
