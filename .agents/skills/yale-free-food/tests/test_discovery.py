#!/usr/bin/env python3
"""
Unit Tests for Discovery Planner, Research Ingester, and 2-Hop Crawling
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

from discovery import generate_query_plan, crawl_page_for_links, execute_ingest_research_command
from event_store import EventStore


class TestDiscoveryEngine(unittest.TestCase):
    def test_query_plan_non_food_ratio(self):
        plan = generate_query_plan("2026-09-27", days=7, profile="daily")
        meta = plan["meta"]
        total_queries = meta["search_tasks_count"]
        non_food_queries = meta["non_food_queries_count"]

        self.assertGreater(total_queries, 0)
        # Verify requirement: at least 50% queries are non-food
        self.assertGreaterEqual(non_food_queries / total_queries, 0.5)

    def test_crawl_page_for_links_extracts_luma_and_eb(self):
        html = """
        <html>
        <body>
          <a href="https://luma.com/yale-ai-dinner?utm_source=test">Join Dinner</a>
          <a href="/events/talk-2026">Internal Event</a>
          <a href="https://eventbrite.com/e/tickets-12345678">Eventbrite Tickets</a>
          <a href="https://google.com">Google</a>
        </body>
        </html>
        """
        links = crawl_page_for_links(html, "https://city.yale.edu/events")
        self.assertTrue(any("luma.com/yale-ai-dinner" in l for l in links))
        self.assertTrue(any("eventbrite.com/e/tickets-12345678" in l for l in links))
        self.assertTrue(any("city.yale.edu/events/talk-2026" in l for l in links))
        self.assertFalse(any("google.com" in l for l in links))

    def test_ingest_research_command(self):
        tmp_dir = tempfile.TemporaryDirectory()
        try:
            jsonl_file = Path(tmp_dir.name) / "research.jsonl"
            records = [
                {
                    "task_id": "test_01",
                    "result_url": "https://luma.com/tsai-pitch",
                    "query_or_parent_url": "https://city.yale.edu/events",
                    "title": "Tsai CITY Pitch Night",
                    "starts_at": "2026-10-02T17:00:00-04:00",
                    "ends_at": "2026-10-02T19:00:00-04:00",
                    "location": "17 Prospect St",
                    "host": "Tsai CITY",
                    "food_status": "provided",
                    "food_cost": "free",
                    "admission_cost": "free",
                    "confidence": "confirmed_free",
                    "evidence_text": "Free pizza and drinks provided for attendees.",
                    "extraction_method": "organizer_expansion",
                    "host_url": "https://luma.com/TheYaleTable",
                    "user_interest": True
                },
                {
                    "task_id": "test_02",
                    "result_url": "https://seas.yale.edu/events/ai-robotics",
                    "title": "AI & Robotics Symposium",
                    "starts_at": "2026-10-03T10:00:00-04:00",
                    "location": "17 Hillhouse Ave",
                    "host": "Yale SEAS",
                    "evidence_text": "Join us for morning lectures and presentations.",
                    "extraction_method": "web_search",
                    "user_interest": True
                }
            ]
            with open(jsonl_file, "w", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r) + "\n")

            class MockArgs:
                input = str(jsonl_file)
                state_dir = tmp_dir.name

            execute_ingest_research_command(MockArgs())

            store = EventStore(state_dir=tmp_dir.name)
            agenda1, _ = store.get_events_for_date("2026-10-02")
            self.assertEqual(len(agenda1), 1)
            self.assertEqual(agenda1[0]["title"], "Tsai CITY Pitch Night")
            self.assertEqual(agenda1[0]["food_status"], "provided")

            agenda2, _ = store.get_events_for_date("2026-10-03")
            self.assertEqual(len(agenda2), 1)
            self.assertEqual(agenda2[0]["title"], "AI & Robotics Symposium")
            self.assertEqual(agenda2[0]["record_kind"], "user_interest")

            # Check 2-hop enqueued host
            pending = store.get_pending_frontier(limit=10)
            self.assertTrue(any("TheYaleTable" in p["url"] for p in pending))
        finally:
            tmp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
