"""
Test Boundary R4: Verification of YaleConnect live feed adapter, replay isolation,
cache metadata preservation, and research search factual corrections.
"""

import unittest
import tempfile
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_verified_runtime as pipeline
import fetch_free_food as feed
import discovery
from event_store import EventStore
from ingest_multi_source import fetch_url_html, FetchResult, NetworkFetchError


class TestBoundaryR4(unittest.TestCase):

    def test_parse_event_list_exists_and_live_adapter_calls_feed(self):
        """Live adapter must call fetch_events, filter by window, and ingest into store."""
        self.assertTrue(hasattr(feed, "parse_event_list"), "parse_event_list must exist in fetch_free_food")

        # Two real-shape YaleConnect records: one in window (2026-09-28), one outside (2026-10-20)
        mock_raw_feed = [
            {
                "p0": "false",
                "p1": "99001",
                "p2": "abc1",
                "p3": "In-Window Innovation Mixer",
                "p4": "Mon, Sep 28, 2026 5:00 PM – 7:00 PM",
                "p5": "Campus/Social",
                "p6": "17 Prospect St",
                "p9": "Innovation Club",
                "p12": "",
                "p18": "/rsvp_boot?id=99001",
                "p22": "Social, Networking"
            },
            {
                "p0": "false",
                "p1": "99002",
                "p2": "abc2",
                "p3": "Out-Window Future Seminar",
                "p4": "Tue, Oct 20, 2026 2:00 PM – 4:00 PM",
                "p5": "Lecture",
                "p6": "WLH 101",
                "p9": "Academic Society",
                "p12": "",
                "p18": "/rsvp_boot?id=99002",
                "p22": "Academic"
            }
        ]

        with tempfile.TemporaryDirectory(prefix="yff-test-live-") as tmp:
            store = EventStore(Path(tmp) / "live")
            stdout = io.StringIO()
            with patch.object(feed, "fetch_events", return_value=mock_raw_feed) as mock_fetch:
                status = pipeline.fetch_and_ingest_yaleconnect(store, "2026-09-27", days=7, mode="live")

            self.assertEqual(mock_fetch.call_count, 1, "fetch_events must be called exactly once")
            self.assertEqual(status["status"], "ok")
            self.assertEqual(len(status["events"]), 1, "Only in-window event should be kept")
            self.assertEqual(status["events"][0]["id"], "99001")

            agenda, _ = store.get_events_for_range("2026-09-27", days=7)
            ids_in_store = [e["id"] for e in agenda]
            self.assertIn("99001", ids_in_store)
            self.assertNotIn("99002", ids_in_store)

    def test_replay_mode_zero_network_calls(self):
        """In replay mode, pipeline must make strictly zero network attempts."""
        with tempfile.TemporaryDirectory(prefix="yff-test-replay-") as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            with patch.dict(os.environ, {"YFF_CACHE_DIR": str(cache_dir)}), \
                 patch.object(discovery, "load_seeds", return_value=[{"entry_url": "https://example.org/review-empty"}]), \
                 patch("urllib.request.urlopen", side_effect=RuntimeError("NETWORK CALL FORBIDDEN IN REPLAY")) as network:
                result = pipeline.run_pipeline(str(Path(tmp) / "replay"), mode="replay", from_date="2026-10-04", days=7)

            self.assertEqual(network.call_count, 0, "Replay mode must make 0 urlopen network calls")
            self.assertIn("daily", result)
            self.assertIn("weekly", result)

    def test_fetch_url_html_cache_fallback_metadata(self):
        """When live fetch fails, cache fallback provides content and preserves metadata."""
        with tempfile.TemporaryDirectory(prefix="yff-test-fallback-") as tmp:
            cdir = Path(tmp) / "cache"
            cdir.mkdir(parents=True, exist_ok=True)
            cached_page = cdir / "test_page.html"
            cached_page.write_text("<html><body>Fallback Content</body></html>", encoding="utf-8")
            idx = {
                "https://example.yale.edu/test": {
                    "file": "test_page.html",
                    "cached_at": "2026-09-27T08:00:00-04:00",
                    "sha256": "fakehash",
                    "source_url": "https://example.yale.edu/test"
                }
            }
            (cdir / "index.json").write_text(json.dumps(idx), encoding="utf-8")

            with patch("urllib.request.urlopen", side_effect=RuntimeError("Simulated network drop")):
                res, meta = fetch_url_html("https://example.yale.edu/test", cache_dir=str(cdir), return_meta=True, mode="live")

            self.assertEqual(meta["fetch_mode"], "cache_fallback")
            self.assertEqual(meta["cached_at"], "2026-09-27T08:00:00-04:00")
            self.assertIn("Fallback Content", str(res))
            self.assertEqual(res.mode, "cache_fallback")

    def test_research_search_factual_corrections(self):
        """Verify all 6 research items meet R4 specifications."""
        sf = ROOT / "data/research-live-search.jsonl"
        self.assertTrue(sf.exists(), "research-live-search.jsonl must exist")
        items = [json.loads(line) for line in sf.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(items), 6)

        by_title = {it["title"]: it for it in items}

        # 1. James Keenan lecture: 18:15, ends_at null, user_requested == 0
        jk = [it for it in items if "James Keenan" in it["title"]][0]
        self.assertEqual(jk["starts_at"], "2026-09-27T18:15:00-04:00")
        self.assertIsNone(jk.get("ends_at"))
        self.assertEqual(jk.get("user_requested"), 0)
        self.assertIn("stm.yale.edu", jk["result_url"])

        # 2. From Problem to Possibility: 18:30-20:30, user_requested == 0, is_independent_new == False
        fpp = [it for it in items if "Problem to Possibility" in it["title"]][0]
        self.assertEqual(fpp["starts_at"], "2026-09-29T18:30:00-04:00")
        self.assertEqual(fpp["ends_at"], "2026-09-29T20:30:00-04:00")
        self.assertEqual(fpp.get("user_requested"), 0)
        self.assertFalse(fpp.get("is_independent_new"))

        # 3. Mission & Vision: 10:30-11:30, user_requested == 0, is_independent_new == False
        mv = [it for it in items if "Mission & Vision" in it["title"]][0]
        self.assertEqual(mv["starts_at"], "2026-10-02T10:30:00-04:00")
        self.assertEqual(mv["ends_at"], "2026-10-02T11:30:00-04:00")
        self.assertEqual(mv.get("user_requested"), 0)
        self.assertFalse(mv.get("is_independent_new"))

        # 4. Yale India Forum: Real Eventbrite ID, unverified price, user_requested == 0
        yif = [it for it in items if "Yale India Forum" in it["title"]][0]
        self.assertIn("1998160586102", yif["result_url"])
        self.assertEqual(yif.get("admission_cost"), "unknown")
        self.assertEqual(yif.get("user_requested"), 0)

        # 5. Nucleate Demo Day: demoted to unverified lead, starts_at is None
        nuc = [it for it in items if "Nucleate" in it["title"]][0]
        self.assertEqual(nuc.get("record_kind"), "unverified_food_lead")
        self.assertIsNone(nuc.get("starts_at"))

        # 6. Engineering Innovation Series: demoted to lead, starts_at is None
        eng = [it for it in items if "Engineering Innovation" in it["title"]][0]
        self.assertEqual(eng.get("record_kind"), "unverified_food_lead")
        self.assertIsNone(eng.get("starts_at"))


if __name__ == "__main__":
    unittest.main()
