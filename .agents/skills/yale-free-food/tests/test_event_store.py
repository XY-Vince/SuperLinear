#!/usr/bin/env python3
"""
Unit Tests for Yale Free Food Discovery EventStore and Frontier State
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from event_store import EventStore, canonicalize_url


class TestCanonicalizeUrl(unittest.TestCase):
    def test_tracking_params_removed(self):
        url = "https://luma.com/event-xyz?utm_source=twitter&utm_medium=social&tk=12345&foo=bar"
        clean = canonicalize_url(url)
        self.assertEqual(clean, "https://luma.com/event-xyz?foo=bar")

    def test_yaleconnect_normalized(self):
        url = "https://yaleconnect.yale.edu/rsvp_boot?id=998877&hash=abc"
        clean = canonicalize_url(url)
        self.assertEqual(clean, "yaleconnect:998877")


class TestEventStore(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.store = EventStore(state_dir=self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_upsert_event_and_dedup(self):
        ev1 = {
            "id": "test:01",
            "url": "https://luma.com/tech-mixer?utm_source=slack",
            "title": "Tech Mixer",
            "starts_at": "2026-10-01T18:00:00-04:00",
            "location": "17 Prospect St",
            "food_status": "provided",
            "food_cost": "free",
            "confidence": "confirmed_free"
        }
        self.store.upsert_event(ev1, method="test")

        ev2 = {
            "url": "https://luma.com/tech-mixer?utm_source=email",
            "title": "Tech Mixer (Updated Title)",
            "location": "Tsai CITY, 17 Prospect St",
            "food_status": "provided",
            "food_cost": "free"
        }
        self.store.upsert_event(ev2, method="test")

        agenda, unverified = self.store.get_events_for_date("2026-10-01")
        self.assertEqual(len(agenda), 1)
        self.assertEqual(agenda[0]["title"], "Tech Mixer (Updated Title)")
        self.assertEqual(agenda[0]["location"], "Tsai CITY, 17 Prospect St")

        # Verify field changes recorded
        with self.store._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM changes WHERE field_name = 'location'")
            ch = cur.fetchone()
            self.assertIsNotNone(ch)
            self.assertEqual(ch["old_value"], "17 Prospect St")
            self.assertEqual(ch["new_value"], "Tsai CITY, 17 Prospect St")

    def test_frontier_queue(self):
        self.store.enqueue_url("https://city.yale.edu/events", discovery_parent="seed", priority=15, depth=0)
        pending = self.store.get_pending_frontier(limit=10)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["url"], "https://city.yale.edu/events")

        self.store.mark_frontier_status("https://city.yale.edu/events", "completed")
        pending_after = self.store.get_pending_frontier(limit=10)
        self.assertEqual(len(pending_after), 0)

    def test_sync_supplemental_json(self):
        ev = {
            "id": "supp:01",
            "title": "Breakfast Club",
            "starts_at": "2026-09-28T09:00:00-04:00",
            "food_status": "provided",
            "food_cost": "free",
            "confidence": "confirmed_free"
        }
        self.store.upsert_event(ev)
        self.store.sync_supplemental_json()

        json_file = Path(self.tmp_dir.name) / "supplemental_events.json"
        self.assertTrue(json_file.exists())
        import json
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data.get("events", [])), 1)
        self.assertEqual(data["events"][0]["title"], "Breakfast Club")


if __name__ == "__main__":
    unittest.main()
