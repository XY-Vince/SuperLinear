"""
Counterfactual Behavioral Replay Tests (AG_SOURCE_TO_BRIEFING_PLAN.md Requirement 5.2)
========================================================================================
Verifies that the discovery engine and briefing generator are dynamically driven
by page content and do not return fixed/hardcoded counts or strings:
1. Empty entry page produces 0 events.
2. Modifying card title changes extracted event title and briefing output.
3. Removing food claim ("Cookies will be provided") reverts food_status to unclear.
4. Adding an additional event card increases discovered count.
5. Shifting event date outside target window excludes it from the 7-day briefing.
"""

import unittest
import tempfile
import argparse
import sys
from pathlib import Path
from unittest.mock import patch

_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from scripts import discovery
from scripts.event_store import EventStore
from scripts.briefing import generate_briefing, render_briefing_markdown
from scripts.ingest_multi_source import parse_luma_html


class TestCounterfactualReplay(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory(prefix="yff-counterfactual-")
        self.state_dir = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_empty_page_produces_zero_events(self):
        args = argparse.Namespace(
            state_dir=str(self.state_dir), profile="quick", resume=False,
            from_date="2026-09-27", days=7
        )
        with patch.object(discovery, "fetch_url_html", return_value="<html><body>Empty Page</body></html>"), \
             patch.object(discovery, "load_seeds", return_value=[{"entry_url": "https://dept.yale.edu/events"}]):
            discovery.execute_run_command(args)

        store = EventStore(state_dir=self.state_dir)
        events, _ = store.get_events_for_date("2026-09-27")
        self.assertEqual(len(events), 0)

    def test_modified_title_reflected_in_briefing(self):
        card_html = """
        <div class="reference-card__content">
          <h3 class="reference-card__heading">
            <a href="/events/counterfactual-custom-event">Counterfactual Synthetic Tech Talk</a>
          </h3>
          <div class="reference-card__subheading">
            <time class="date-time" datetime="2026-09-27T15:00:00-04:00">September 27, 2026 3:00 pm—4:00 pm</time>
          </div>
        </div>
        """
        args = argparse.Namespace(
            state_dir=str(self.state_dir), profile="quick", resume=False,
            from_date="2026-09-27", days=7
        )
        with patch.object(discovery, "fetch_url_html", return_value=card_html), \
             patch.object(discovery, "load_seeds", return_value=[{"entry_url": "https://poorvucenter.yale.edu/events"}]):
            discovery.execute_run_command(args)

        briefing = generate_briefing(str(self.state_dir), "2026-09-27")
        md = render_briefing_markdown(briefing)
        self.assertIn("Counterfactual Synthetic Tech Talk", md)

    def test_removing_food_sentence_reverts_status(self):
        html_with_cookies = """
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Event", "name": "Founder Workshop",
         "startDate": "2026-10-02T18:00:00.000Z", "description": "Interactive Q&A. Cookies will be provided."}
        </script>
        """
        html_no_cookies = """
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Event", "name": "Founder Workshop",
         "startDate": "2026-10-02T18:00:00.000Z", "description": "Interactive Q&A."}
        </script>
        """
        rec_with = parse_luma_html(html_with_cookies, url="https://luma.com/test1", allow_non_food=True)
        rec_without = parse_luma_html(html_no_cookies, url="https://luma.com/test1", allow_non_food=True)

        self.assertEqual(rec_with["food_status"], "provided")
        self.assertEqual(rec_without["food_status"], "unclear")

    def test_additional_card_increases_discovered_count(self):
        one_card = """
        <div class="reference-card__content">
          <h3><a href="/events/ev1">Event 1</a></h3>
          <time datetime="2026-09-28T10:00:00-04:00">Sep 28, 2026 10:00 am</time>
        </div>
        """
        two_cards = one_card + """
        <div class="reference-card__content">
          <h3><a href="/events/ev2">Event 2</a></h3>
          <time datetime="2026-09-28T14:00:00-04:00">Sep 28, 2026 2:00 pm</time>
        </div>
        """
        cards_1 = discovery.extract_html_event_cards(one_card)
        cards_2 = discovery.extract_html_event_cards(two_cards)
        self.assertEqual(len(cards_1), 1)
        self.assertEqual(len(cards_2), 2)

    def test_event_outside_window_excluded_from_week_briefing(self):
        store = EventStore(state_dir=self.state_dir)
        # Event in window (2026-09-28)
        store.upsert_event({
            "id": "in_win", "title": "In Window Event",
            "starts_at": "2026-09-28T12:00:00-04:00",
            "food_status": "unclear"
        })
        # Event outside window (2026-12-15)
        store.upsert_event({
            "id": "out_win", "title": "Far Future Event",
            "starts_at": "2026-12-15T12:00:00-05:00",
            "food_status": "unclear"
        })

        briefing = generate_briefing(str(self.state_dir), "2026-09-27", days=7)
        md = render_briefing_markdown(briefing)
        self.assertIn("In Window Event", md)
        self.assertNotIn("Far Future Event", md)


if __name__ == "__main__":
    unittest.main()
