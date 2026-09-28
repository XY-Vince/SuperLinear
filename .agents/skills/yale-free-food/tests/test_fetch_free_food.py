#!/usr/bin/env python3
"""
Comprehensive Regression & CLI Acceptance Test Suite for Yale Free Food Monitor (v1.2.0-candidate)
Validates all P1/P2 issues, edge cases, and CLI integration behaviors:
1. Strict TLS verification (no bypasses)
2. Network error handling and non-zero exit codes
3. Semantic disambiguation:
   - "Lunch provided" -> food_status: provided, food_cost: unknown, admission: unknown
   - "Dinner tickets $30; dinner provided" -> admission paid ($30.00), excluded
   - "Dinner $30 per person; dinner provided" -> admission paid ($30.00), excluded
   - "Tickets: $0.50; lunch provided" -> admission paid ($0.50), excluded
   - "Lunch provided. Tickets: $0" -> admission free ($0), not excluded
   - "Free pizza; bring your own laptop" -> BYO laptop is not BYO food, confirmed_free
   - "Free pizza; bring your own plate" -> BYO plate is cutlery, not BYO food, confirmed_free
   - "No free food; pizza available for purchase" -> food_status: provided, food_cost: paid, excluded
   - "Free admission; dinner available for purchase" -> admission: free, food_cost: paid, excluded
   - "No free food at this reception" -> food_cost: paid/not_free, excluded
   - "Reception — no food or refreshments provided" -> food_status: none, excluded
   - "Gluten-free pizza tasting" -> food_cost: unknown (not free pizza)
   - "Lecture on nutrition policy and ice cream" -> excluded (academic, not likely_free)
4. Date parsing & timezones:
   - Single-time events (10 AM) not marked past at 13:00 on the same date
   - Multi-day events (Sep 18 - Sep 20) active on Sep 18, 19, 20
   - Winter dates in New York correctly get EST (UTC-5)
5. Detail scope & HTML robustness:
   - Registration price ($25) not overridden by "Free" in Details
   - Sign-in / login-walled pages produce unknown eligibility, not "全员公开"
6. Bounded budget & CLI execution:
   - detail-budget=1 stops after 1 attempted request even on failure
   - --no-details makes 0 detail requests
   - Text output with --include-paid does not crash on missing keys
   - Full CLI pipeline replay of 5 real events (Ice cream, Lorax, YSPH Fair, Mixer found; Chi Alpha excluded)
7. SKILL.md Frontmatter metadata conformance
"""

import sys
import os
import io
import json
import unittest
from unittest.mock import patch, MagicMock
from io import StringIO
import urllib.error
import ssl
from datetime import datetime, timedelta
from pathlib import Path

# Setup import paths
SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
FIXTURES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "fixtures"))
sys.path.insert(0, SCRIPT_DIR)

import fetch_free_food
from fetch_free_food import (
    classify_event,
    fetch_events,
    normalize_url,
    parse_yale_date,
    parse_detail_page,
    get_ny_timezone,
    clean_html,
    NY_TZ
)


class TestFetchFreeFoodV120(unittest.TestCase):

    def setUp(self):
        # Sep 18, 2026 11:30 AM EDT
        self.now_ny = datetime(2026, 9, 18, 11, 30, tzinfo=NY_TZ)
        self.target_date = "2026-09-18"

    # =========================================================================
    # 1. TLS and Network Robustness
    # =========================================================================
    def test_tls_never_disabled(self):
        script_path = os.path.join(SCRIPT_DIR, "fetch_free_food.py")
        with open(script_path, "r", encoding="utf-8") as f:
            code = f.read()
        self.assertNotIn("_create_unverified_context", code)
        self.assertIn("ssl.create_default_context", code)

    def test_fetch_events_raises_on_http_error(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="https://yaleconnect.yale.edu", code=403, msg="Forbidden", hdrs={}, fp=None
            )
            with self.assertRaises(RuntimeError) as ctx:
                fetch_events(limit=5)
            self.assertIn("HTTP error 403", str(ctx.exception))

    def test_main_exits_code_1_on_failure(self):
        with patch("fetch_free_food.fetch_events", side_effect=RuntimeError("Simulated DNS Down")):
            stderr_cap = StringIO()
            with patch("sys.stderr", stderr_cap):
                with patch("sys.argv", ["fetch_free_food.py"]):
                    with self.assertRaises(SystemExit) as cm:
                        fetch_free_food.main()
                    self.assertEqual(cm.exception.code, 1)

    # =========================================================================
    # 2. Semantic Disambiguation Cases
    # =========================================================================
    def test_probe_lunch_provided_has_unknown_cost(self):
        item = {'p1': 't1', 'p3': 'Lunch provided', 'p4': 'Fri, Sep 18, 2026 2 PM – 4 PM'}
        res = classify_event(item, now_ny=self.now_ny, target_date_str=self.target_date)
        self.assertIsNotNone(res)
        self.assertEqual(res['food_status'], 'provided')
        self.assertEqual(res['food_cost'], 'unknown')
        self.assertNotEqual(res['confidence'], 'confirmed_free')

    def test_probe_dinner_tickets_30_is_excluded(self):
        item = {'p1': 't2', 'p3': 'Dinner tickets $30; dinner provided', 'p4': 'Fri, Sep 18, 2026 6 PM – 9 PM'}
        res = classify_event(item, now_ny=self.now_ny, target_date_str=self.target_date)
        self.assertIsNotNone(res)
        self.assertEqual(res['confidence'], 'excluded')
        self.assertEqual(res['admission_cost'], 'paid ($30.00)')

    def test_probe_dinner_30_per_person_is_excluded(self):
        item = {'p1': 't3', 'p3': 'Dinner $30 per person; dinner provided', 'p4': 'Fri, Sep 18, 2026 6 PM – 9 PM'}
        res = classify_event(item, now_ny=self.now_ny, target_date_str=self.target_date)
        self.assertIsNotNone(res)
        self.assertEqual(res['confidence'], 'excluded')
        self.assertEqual(res['admission_cost'], 'paid ($30.00)')

    def test_probe_tickets_zero_dollars_is_free(self):
        item = {'p1': 't4', 'p3': 'Lunch provided. Tickets: $0', 'p4': 'Fri, Sep 18, 2026 2 PM – 4 PM'}
        res = classify_event(item, now_ny=self.now_ny, target_date_str=self.target_date)
        self.assertIsNotNone(res)
        self.assertNotEqual(res['confidence'], 'excluded')
        self.assertEqual(res['admission_cost'], 'free')

    def test_probe_tickets_fifty_cents_is_paid(self):
        item = {'p1': 't4_half', 'p3': 'Tickets: $0.50; lunch provided', 'p4': 'Fri, Sep 18, 2026 2 PM – 4 PM'}
        res = classify_event(item, now_ny=self.now_ny, target_date_str=self.target_date)
        self.assertIsNotNone(res)
        self.assertEqual(res['confidence'], 'excluded')
        self.assertEqual(res['admission_cost'], 'paid ($0.50)')

    def test_probe_byo_laptop_not_byo_food(self):
        item = {'p1': 't5', 'p3': 'Free pizza; bring your own laptop', 'p4': 'Fri, Sep 18, 2026 2 PM – 4 PM'}
        res = classify_event(item, now_ny=self.now_ny, target_date_str=self.target_date)
        self.assertIsNotNone(res)
        self.assertNotEqual(res['food_cost'], 'byo')
        self.assertEqual(res['confidence'], 'confirmed_free')

    def test_probe_byo_plate_not_byo_food(self):
        item = {'p1': 't5_plate', 'p3': 'Free pizza; bring your own plate', 'p4': 'Fri, Sep 18, 2026 2 PM – 4 PM'}
        res = classify_event(item, now_ny=self.now_ny, target_date_str=self.target_date)
        self.assertIsNotNone(res)
        self.assertNotEqual(res['food_cost'], 'byo')
        self.assertEqual(res['confidence'], 'confirmed_free')
        self.assertTrue(any('自带餐具' in n for n in res['food_cost_notes']))

    def test_probe_no_free_food_excluded(self):
        item = {'p1': 't6', 'p3': 'No free food at this reception', 'p4': 'Fri, Sep 18, 2026 2 PM – 4 PM'}
        res = classify_event(item, now_ny=self.now_ny, target_date_str=self.target_date)
        self.assertIsNotNone(res)
        self.assertEqual(res['confidence'], 'excluded')
        self.assertEqual(res['food_cost'], 'paid')

    def test_probe_no_free_food_pizza_available_for_purchase(self):
        item = {'p1': 't6_pizza', 'p3': 'No free food; pizza available for purchase', 'p4': 'Fri, Sep 18, 2026 2 PM – 4 PM'}
        res = classify_event(item, now_ny=self.now_ny, target_date_str=self.target_date)
        self.assertIsNotNone(res)
        self.assertEqual(res['food_status'], 'provided')
        self.assertEqual(res['food_cost'], 'paid')
        self.assertEqual(res['confidence'], 'excluded')

    def test_probe_free_admission_dinner_available_for_purchase(self):
        item = {'p1': 't6_adm', 'p3': 'Free admission; dinner available for purchase', 'p4': 'Fri, Sep 18, 2026 2 PM – 4 PM'}
        res = classify_event(item, now_ny=self.now_ny, target_date_str=self.target_date)
        self.assertIsNotNone(res)
        self.assertEqual(res['admission_cost'], 'free')
        self.assertEqual(res['food_cost'], 'paid')
        self.assertEqual(res['confidence'], 'excluded')

    def test_probe_no_food_provided_reception_excluded(self):
        item = {'p1': 't7', 'p3': 'Reception — no food or refreshments provided', 'p4': 'Fri, Sep 18, 2026 2 PM – 4 PM'}
        res = classify_event(item, now_ny=self.now_ny, target_date_str=self.target_date)
        self.assertIsNotNone(res)
        self.assertEqual(res['food_status'], 'none')
        self.assertEqual(res['confidence'], 'excluded')

    def test_probe_gluten_free_pizza_cost_unknown(self):
        item = {'p1': 't8', 'p3': 'Gluten-free pizza tasting', 'p4': 'Fri, Sep 18, 2026 2 PM – 4 PM'}
        res = classify_event(item, now_ny=self.now_ny, target_date_str=self.target_date)
        self.assertIsNotNone(res)
        self.assertEqual(res['food_status'], 'provided')
        self.assertEqual(res['food_cost'], 'unknown')
        self.assertNotEqual(res['confidence'], 'confirmed_free')

    def test_probe_nutrition_policy_lecture_excluded(self):
        item = {'p1': 't9', 'p3': 'Lecture on nutrition policy and ice cream', 'p4': 'Fri, Sep 18, 2026 2 PM – 4 PM'}
        res = classify_event(item, now_ny=self.now_ny, target_date_str=self.target_date)
        self.assertIsNone(res)

    # =========================================================================
    # 3. Date Parsing & Timezone Handling
    # =========================================================================
    def test_single_time_not_marked_past_at_1300(self):
        parsed = parse_yale_date('Fri, Sep 18, 2026 10 AM', now_ny=self.now_ny)
        self.assertIsNotNone(parsed)
        self.assertTrue(parsed.ends_at_unknown)
        self.assertTrue(parsed.spans_date('2026-09-18'))
        # At 13:00 on the same date, must not assume it ended
        now_1300 = self.now_ny.replace(hour=13)
        self.assertFalse(parsed.is_past(now_1300))

    def test_multiday_event_active_on_target_date(self):
        parsed = parse_yale_date('Fri, Sep 18, 2026 9 AM – Sun, Sep 20, 2026 5 PM', now_ny=self.now_ny)
        self.assertIsNotNone(parsed)
        self.assertFalse(parsed.ends_at_unknown)
        self.assertTrue(parsed.spans_date('2026-09-18'))
        self.assertTrue(parsed.spans_date('2026-09-19'))
        self.assertTrue(parsed.spans_date('2026-09-20'))
        self.assertFalse(parsed.spans_date('2026-09-21'))
        self.assertFalse(parsed.is_past(self.now_ny))

    def test_fallback_winter_event_gets_est(self):
        parsed = parse_yale_date('Fri, Jan 15, 2027 2 PM – 4 PM', now_ny=self.now_ny)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.start_dt.strftime('%z'), '-0500')

    def test_target_date_filters_future_events(self):
        item = {'p1': 't10', 'p3': 'Sunday Free Pizza', 'p4': 'Sun, Sep 20, 2026 12 PM – 2 PM'}
        res = classify_event(item, now_ny=self.now_ny, target_date_str='2026-09-18')
        self.assertIsNone(res)

    # =========================================================================
    # 4. URL Normalization
    # =========================================================================
    def test_url_protocol_relative(self):
        self.assertEqual(
            normalize_url("https://yaleconnect.yale.edu", "//luma.com/abc"),
            "https://luma.com/abc"
        )

    def test_url_javascript_rejected(self):
        self.assertEqual(
            normalize_url("https://yaleconnect.yale.edu", "javascript:alert(1)"),
            ""
        )

    def test_url_relative_joined(self):
        self.assertEqual(
            normalize_url("https://yaleconnect.yale.edu", "/lacasa/rsvp_boot?id=2330205"),
            "https://yaleconnect.yale.edu/lacasa/rsvp_boot?id=2330205"
        )

    # =========================================================================
    # 5. Detail Scope & HTML Robustness
    # =========================================================================
    def test_detail_scope_registration_price_not_polluted_by_details(self):
        html = '<h2>Registration</h2><p>Price $25</p><h2>Details</h2><p>Free pizza provided</p><h2>Hosted By</h2>'
        parsed = parse_detail_page(html)
        self.assertEqual(parsed['admission_price'], 'paid ($25.00)')
        self.assertEqual(parsed['details_text'], 'Free pizza provided')

    def test_detail_scope_sign_in_page_sets_unknown_eligibility(self):
        html = '<h1>Sign In</h1><p>Please sign in to view this event.</p>'
        parsed = parse_detail_page(html)
        self.assertTrue(parsed['login_restricted'])
        self.assertIn('需登录', parsed['eligibility'])
        self.assertNotEqual(parsed['eligibility'], '公开 / 全员')

    # =========================================================================
    # 6. Budget & CLI Execution Integration
    # =========================================================================
    def test_budget_strictly_bounded_on_failures(self):
        """When budget=1 and requests fail, exactly 1 request is attempted."""
        items = [{'p1': str(i), 'p3': 'Free pizza', 'p4': 'Fri, Sep 18, 2026 2 PM – 4 PM', 'p18': f'/rsvp?id={i}'} for i in range(4)]
        requests_made = []

        def mock_fail(url, **kw):
            requests_made.append(url)
            return None

        out_cap = StringIO()
        with patch.object(fetch_free_food, 'fetch_events', return_value=items):
            with patch.object(fetch_free_food, 'fetch_event_detail_html', side_effect=mock_fail):
                with patch('sys.argv', ['fetch_free_food.py', '--date', '2026-09-18', '--json', '--detail-budget', '1']):
                    with patch('sys.stdout', out_cap):
                        with self.assertRaises(SystemExit) as cm:
                            fetch_free_food.main()
                        self.assertEqual(cm.exception.code, 0)

        self.assertEqual(len(requests_made), 1, "Must strictly respect budget=1 even when requests fail")
        data = json.loads(out_cap.getvalue())
        self.assertEqual(data['meta']['detail_attempted'], 1)
        self.assertEqual(data['meta']['coverage_status'], 'partial')

    def test_no_details_makes_zero_requests(self):
        items = [{'p1': '1', 'p3': 'Free pizza', 'p4': 'Fri, Sep 18, 2026 2 PM – 4 PM', 'p18': '/rsvp?id=1'}]
        requests_made = []

        def mock_fetch(url, **kw):
            requests_made.append(url)
            return None

        out_cap = StringIO()
        with patch.object(fetch_free_food, 'fetch_events', return_value=items):
            with patch.object(fetch_free_food, 'fetch_event_detail_html', side_effect=mock_fetch):
                with patch('sys.argv', ['fetch_free_food.py', '--date', '2026-09-18', '--json', '--no-details']):
                    with patch('sys.stdout', out_cap):
                        with self.assertRaises(SystemExit) as cm:
                            fetch_free_food.main()
                        self.assertEqual(cm.exception.code, 0)

        self.assertEqual(len(requests_made), 0, "--no-details must make 0 requests")

    def test_text_output_with_include_paid_does_not_crash(self):
        item = {'p1': 'n', 'p3': 'No free food at this reception', 'p4': 'Fri, Sep 18, 2026 2 PM – 4 PM'}
        out_cap = StringIO()
        with patch.object(fetch_free_food, 'fetch_events', return_value=[item]):
            with patch('sys.argv', ['fetch_free_food.py', '--date', '2026-09-18', '--include-paid', '--no-details']):
                with patch('sys.stdout', out_cap):
                    with self.assertRaises(SystemExit) as cm:
                        fetch_free_food.main()
                    self.assertEqual(cm.exception.code, 0)
        self.assertIn("No free food at this reception", out_cap.getvalue())

    # =========================================================================
    # 7. End-to-End CLI Pipeline with 5 Real Fixtures
    # =========================================================================
    def test_cli_full_pipeline_five_fixtures(self):
        """
        Replay the full main() CLI pipeline with the 2026-09-18 real feed
        and real detail page fixtures.
        Must find 4 events (Ice cream, Lorax, YSPH Fair, Mixer) and exclude Chi Alpha.
        """
        fix_path = Path(FIXTURES_DIR)
        raw_feed_path = fix_path / 'yale-food-2026-09-18-raw.json'
        if not raw_feed_path.exists():
            self.skipTest("raw feed fixture missing")

        raw_feed = json.loads(raw_feed_path.read_text(encoding='utf-8'))
        lookup = {
            '2331237': 'yale-food-2026-09-18-detail.txt',
            '2330437': 'yale-food-2026-09-18-2330437.txt',
            '2330205': 'yale-food-2026-09-18-2330205.txt',
            '2329688': 'yale-food-2026-09-18-2329688.txt',
            '2330066': 'yale-food-2026-09-18-2330066.txt'
        }

        requested_ids = []

        def mock_detail_fetch(url, **kw):
            eid = url.split('id=')[-1]
            requested_ids.append(eid)
            if eid in lookup:
                f_path = fix_path / lookup[eid]
                if f_path.exists():
                    return f_path.read_text(encoding='utf-8')
            return None

        out_cap = StringIO()
        with patch.object(fetch_free_food, 'fetch_events', return_value=raw_feed):
            with patch.object(fetch_free_food, 'fetch_event_detail_html', side_effect=mock_detail_fetch):
                with patch('sys.argv', ['fetch_free_food.py', '--date', '2026-09-18', '--json', '--detail-budget', '100']):
                    with patch('sys.stdout', out_cap):
                        with self.assertRaises(SystemExit) as cm:
                            fetch_free_food.main()
                        self.assertEqual(cm.exception.code, 0)

        data = json.loads(out_cap.getvalue())
        found_events = {ev['id']: ev for ev in data['events']}

        # Assert all 4 food events found
        self.assertIn('2331237', found_events, "East Rock Ice Cream must be found")
        self.assertIn('2330437', found_events, "Lorax TGIF must be found")
        self.assertIn('2329688', found_events, "YSPH Fair must be found")
        self.assertIn('2330205', found_events, "Intercultural Mixer must be found")

        # Assert Chi Alpha is excluded from food events
        self.assertNotIn('2330066', found_events, "Chi Alpha has no food and must be excluded")

        # Check East Rock Ice Cream details
        ice_cream = found_events['2331237']
        self.assertEqual(ice_cream['food_cost'], 'free')
        self.assertEqual(ice_cream['confidence'], 'confirmed_free')
        self.assertEqual(ice_cream['rsvp_status'], '免费领取需 RSVP')
        self.assertIn('Phelps Gate', ice_cream['location'])

        # Check Lorax TGIF details
        lorax = found_events['2330437']
        self.assertEqual(lorax['food_status'], 'provided')
        self.assertEqual(lorax['admission_cost'], 'free')
        self.assertIn('Food Provided (详情页徽章)', lorax['food_signals'])

        # Check Intercultural Mixer details
        mixer = found_events['2330205']
        self.assertEqual(mixer['food_status'], 'provided')
        self.assertEqual(mixer['admission_cost'], 'free')

    # =========================================================================
    # 8. Metadata Schema Conformance
    # =========================================================================
    def test_skill_metadata_conformance(self):
        skill_path = os.path.join(os.path.dirname(SCRIPT_DIR), "SKILL.md")
        with open(skill_path, "r", encoding="utf-8") as f:
            content = f.read()

        parts = content.split("---")
        self.assertGreaterEqual(len(parts), 3)
        frontmatter = parts[1]

        top_lines = [line.strip() for line in frontmatter.splitlines() if line.strip() and not line.startswith(" ")]
        top_keys = [line.split(":")[0].strip() for line in top_lines]

        self.assertNotIn("version", top_keys)
        self.assertNotIn("user_invocable", top_keys)
        self.assertIn("name", top_keys)
        self.assertIn("description", top_keys)
        self.assertIn("metadata", top_keys)

    # =========================================================================
    # 9. Real Historical Fixtures Verification (2326442, 2326426, 2326199)
    # =========================================================================
    def test_fixture_trivia_night_2326442_partial_drink_quota(self):
        fixture_path = os.path.join(FIXTURES_DIR, "fixture-2326442.html")
        self.assertTrue(os.path.exists(fixture_path))
        with open(fixture_path, "r", encoding="utf-8") as f:
            html = f.read()

        d = parse_detail_page(html)
        self.assertEqual(d["admission_price"], "free")
        self.assertEqual(d["eligibility"], "面向秋季新入学国际学生")
        self.assertTrue(any("1 alcoholic/non-alcoholic drink" in note for note in d["benefit_notes"]))
        self.assertTrue(any("额外餐饮自费" in note for note in d["benefit_notes"]))

        item = {
            "p1": "2326442",
            "p3": "OISS Global Bulldogs Trivia Night",
            "p4": "Wed, Sep 9, 2026 5:30 PM – 7 PM",
            "p6": "The Well / Schwarzman Center",
            "p18": "/rsvp_boot?id=2326442"
        }
        res = classify_event(item, detail_info=d, now_ny=self.now_ny)
        self.assertIsNotNone(res)
        self.assertEqual(res["food_status"], "provided")
        self.assertEqual(res["food_cost"], "free")
        self.assertEqual(res["confidence"], "confirmed_free")
        self.assertEqual(res["eligibility"], "面向秋季新入学国际学生")
        self.assertIn("The Well / Schwarzman Center", res["location"])
        # Transparent benefit notes preserved
        self.assertTrue(any("1 alcoholic/non-alcoholic drink" in note for note in res["benefit_notes"]))
        self.assertTrue(any("额外自费" in note for note in res["food_cost_notes"]))

    def test_fixture_gp_panel_2326426_light_refreshment_cost_unknown(self):
        fixture_path = os.path.join(FIXTURES_DIR, "fixture-2326426.html")
        self.assertTrue(os.path.exists(fixture_path))
        with open(fixture_path, "r", encoding="utf-8") as f:
            html = f.read()

        d = parse_detail_page(html)
        self.assertEqual(d["admission_price"], "free")
        self.assertEqual(d["eligibility"], "面向新入学国际研究生/专业学院学生")
        self.assertEqual(d["rsvp_status"], "先到先得 (建议 RSVP)")

        item = {
            "p1": "2326426",
            "p3": "First Year Experience Panel for International G&P Students",
            "p4": "Thu, Sep 10, 2026 5:30 PM – 7 PM",
            "p6": "Register to display",
            "p18": "/rsvp_boot?id=2326426"
        }
        res = classify_event(item, detail_info=d, now_ny=self.now_ny)
        self.assertIsNotNone(res)
        self.assertEqual(res["food_status"], "provided")
        self.assertEqual(res["food_cost"], "unknown")
        self.assertEqual(res["admission_cost"], "free")
        self.assertEqual(res["confidence"], "needs_verification")
        self.assertEqual(res["eligibility"], "面向新入学国际研究生/专业学院学生")
        self.assertEqual(res["rsvp_status"], "先到先得 (建议 RSVP)")

    def test_fixture_police_pizza_2326199_must_register(self):
        fixture_path = os.path.join(FIXTURES_DIR, "fixture-2326199.html")
        self.assertTrue(os.path.exists(fixture_path))
        with open(fixture_path, "r", encoding="utf-8") as f:
            html = f.read()

        d = parse_detail_page(html)
        self.assertEqual(d["admission_price"], "free")
        self.assertEqual(d["eligibility"], "限新到校国际社区成员")
        self.assertEqual(d["rsvp_status"], "必须注册")

        item = {
            "p1": "2326199",
            "p3": "Pizza & Conversation with Yale's Police Chief",
            "p4": "Tue, Sep 8, 2026 12 PM – 1 PM",
            "p6": "Private Location (register to display)",
            "p18": "/rsvp_boot?id=2326199"
        }
        res = classify_event(item, detail_info=d, now_ny=self.now_ny)
        self.assertIsNotNone(res)
        self.assertEqual(res["eligibility"], "限新到校国际社区成员")
        self.assertEqual(res["rsvp_status"], "必须注册")
        self.assertEqual(res["food_status"], "provided")
        self.assertEqual(res["food_cost"], "unknown")

    # =========================================================================
    # 10. Date Range Validation & Rejection
    # =========================================================================
    def test_inverted_dates_rejected_before_networking(self):
        stderr_cap = StringIO()
        with patch("sys.stderr", stderr_cap):
            with patch("sys.argv", ["fetch_free_food.py", "--from-date", "2026-09-18", "--to-date", "2026-08-31"]):
                with self.assertRaises(SystemExit) as cm:
                    fetch_free_food.main()
                self.assertEqual(cm.exception.code, 1)
        self.assertIn("日期范围非法", stderr_cap.getvalue())

    def test_invalid_date_format_rejected(self):
        stderr_cap = StringIO()
        with patch("sys.stderr", stderr_cap):
            with patch("sys.argv", ["fetch_free_food.py", "--date", "2026/09/18"]):
                with self.assertRaises(SystemExit) as cm:
                    fetch_free_food.main()
                self.assertEqual(cm.exception.code, 1)
        self.assertIn("非法日期格式", stderr_cap.getvalue())

    # =========================================================================
    # 11. Pagination Deduplication & Stop Reasons
    # =========================================================================
    def test_fetch_events_terminates_on_duplicate_page(self):
        same_page = [
            {"p1": "101", "p3": "Event A", "p4": "Sep 1, 2026"},
            {"p1": "102", "p3": "Event B", "p4": "Sep 2, 2026"}
        ]
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.getcode.return_value = 200
        mock_resp.read.return_value = json.dumps(same_page).encode('utf-8')
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = fetch_events(limit=2, paginate=True, max_pages=10)
            self.assertEqual(len(res), 2)
            self.assertEqual(res.meta["stop_reason"], "duplicate_page")
            self.assertEqual(res.meta["page_count"], 2)

    def test_fetch_events_page_budget_reached(self):
        call_count = [0]
        def mock_urlopen(req, **kw):
            call_count[0] += 1
            page_data = [{"p1": f"ev_{call_count[0]}_{i}", "p3": f"Event {i}", "p4": "Sep 1, 2026"} for i in range(2)]
            resp = MagicMock()
            resp.status = 200
            resp.getcode.return_value = 200
            resp.read.return_value = json.dumps(page_data).encode('utf-8')
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = None
            return resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            res = fetch_events(limit=2, paginate=True, max_pages=3)
            self.assertEqual(res.meta["page_count"], 3)
            self.assertEqual(res.meta["stop_reason"], "page_budget_reached")
            self.assertTrue(res.meta["truncated"])

    # =========================================================================
    # 12. CLI Detail Budget Exhaustion & Coverage Status
    # =========================================================================
    def test_detail_budget_exhaustion_marks_coverage_partial(self):
        feed = [
            {"p1": str(i), "p3": f"Free Pizza Party {i}", "p4": "Fri, Sep 18, 2026 2 PM – 4 PM", "p18": f"/rsvp?id={i}"}
            for i in range(3)
        ]
        globals_ = fetch_free_food.main.__globals__
        out = StringIO()
        with patch.dict(globals_, {
            "fetch_events": lambda **kw: feed,
            "fetch_event_detail_html": lambda *a, **kw: "<html><body><h2>Details</h2><p>Free pizza</p></body></html>"
        }), patch.object(sys, "argv", [
            "fetch_free_food.py", "--date", "2026-09-18", "--json", "--detail-budget", "1"
        ]), patch("sys.stdout", out):
            with self.assertRaises(SystemExit) as cm:
                fetch_free_food.main()
            self.assertEqual(cm.exception.code, 0)

        meta = json.loads(out.getvalue())["meta"]
        self.assertEqual(meta["detail_attempted"], 1)
        self.assertEqual(meta["skipped_budget"], 2)
        self.assertEqual(meta["coverage_status"], "partial")

    # =========================================================================
    # 13. External Source Parsers & Multi-Source Replay
    # =========================================================================
    def test_parse_windham_campbell_fixture(self):
        w_path = os.path.join(FIXTURES_DIR, "fixture-windham.html")
        self.assertTrue(os.path.exists(w_path))
        with open(w_path, "r", encoding="utf-8") as f:
            html = f.read()

        ev = fetch_free_food.parse_windham_campbell_page(html)
        self.assertIsNotNone(ev)
        self.assertEqual(ev["id"], "windhamcampbell:2026:morning-wake-up-2")
        self.assertEqual(ev["title"], "Morning Wake Up")
        self.assertEqual(ev["starts_at"], "2026-09-18T10:30:00-04:00")
        self.assertEqual(ev["food_service_start"], "2026-09-18T10:00:00-04:00")
        self.assertEqual(ev["food_service_end"], "2026-09-18T10:30:00-04:00")
        self.assertEqual(ev["location"], "College Street Tent")
        self.assertEqual(ev["food_status"], "provided")
        self.assertEqual(ev["food_cost"], "free")
        self.assertEqual(ev["admission_cost"], "free")
        self.assertEqual(ev["eligibility"], "open to the public")
        self.assertEqual(ev["rsvp_status"], "unknown")
        self.assertEqual(ev["confidence"], "confirmed_free")
        self.assertIn("complimentary coffee and treats", ev["evidence"])
        self.assertIn("Free and open to the public.", ev["evidence"])
        self.assertEqual(ev["record_kind"], "food_candidate")

    def test_parse_luma_fixture(self):
        l_path = os.path.join(FIXTURES_DIR, "fixture-luma.html")
        self.assertTrue(os.path.exists(l_path))
        with open(l_path, "r", encoding="utf-8") as f:
            html = f.read()

        ev = fetch_free_food.parse_luma_page(html)
        self.assertIsNotNone(ev)
        self.assertEqual(ev["id"], "luma:pnay4247")
        self.assertEqual(ev["title"], "Yale AI Innovation Symposium (Hosted by SAPA-CT, YGCC, Nucleate)")
        self.assertEqual(ev["starts_at"], "2026-09-18T11:00:00-04:00")
        self.assertEqual(ev["ends_at"], "2026-09-18T18:00:00-04:00")
        self.assertEqual(ev["location"], "Tsai Center for Innovative Thinking at Yale, 17 Prospect St")
        self.assertEqual(ev["food_status"], "provided")
        self.assertEqual(ev["food_cost"], "unknown")
        self.assertEqual(ev["admission_cost"], "unknown")
        self.assertEqual(ev["eligibility"], "unknown")
        self.assertIn("Waitlist", ev["rsvp_status"])
        self.assertEqual(ev["confidence"], "needs_verification")
        self.assertIn("Food and drinks will be provided", ev["evidence"])
        self.assertEqual(ev["record_kind"], "food_candidate")

    def test_parse_bioct_fixture(self):
        b_path = os.path.join(FIXTURES_DIR, "fixture-bioct.html")
        self.assertTrue(os.path.exists(b_path))
        with open(b_path, "r", encoding="utf-8") as f:
            html = f.read()

        b_info = fetch_free_food.parse_bioct_page(html)
        self.assertIsNotNone(b_info)
        self.assertEqual(b_info["luma_url"], "https://luma.com/pnay4247")
        self.assertIn("Food and drinks will be provided", b_info["evidence"])
        self.assertEqual(b_info["starts_at"], "2026-09-18T11:00:00-04:00")
        self.assertEqual(b_info["ends_at"], "2026-09-18T18:00:00-04:00")

    def test_parse_email_lead_relative_tomorrow_and_privacy(self):
        sample_email = (
            "From: Secret Sender <secret.sender@example.edu>\n"
            "To: student.recipient@example.edu\n"
            "Date: Thu, 17 Sep 2026 16:00:00 -0400\n"
            "Subject: GECO TGIF Tomorrow!\n\n"
            "Hi engineering friends,\n\n"
            "Join us for GECO TGIF Tomorrow starting at 6PM at the Mann Student Center! All engineering friends invited.\n"
            "Club signup: https://yaleconnect.yale.edu/geco/club_signup\n"
        )
        lead = fetch_free_food.parse_email_lead(sample_email)
        self.assertIsNotNone(lead)
        self.assertEqual(lead["id"], "user-email:geco-tgif:2026-09-18")
        self.assertEqual(lead["title"], "GECO TGIF")
        self.assertEqual(lead["starts_at"], "2026-09-18T18:00:00-04:00")
        self.assertIsNone(lead["ends_at"])
        self.assertEqual(lead["location"], "Mann Student Center")
        self.assertEqual(lead["food_status"], "unclear")
        self.assertEqual(lead["food_cost"], "unknown")
        self.assertEqual(lead["admission_cost"], "unknown")
        self.assertEqual(lead["eligibility"], "engineering friends invited; formal restriction unknown")
        self.assertIn("club_signup is club membership, not event RSVP", lead["rsvp_status"])
        self.assertEqual(lead["club_url"], "https://yaleconnect.yale.edu/geco/club_signup")
        self.assertIsNone(lead["url"])
        self.assertEqual(lead["record_kind"], "unverified_food_lead")
        self.assertEqual(lead["confidence"], "needs_verification")
        # Ensure complete privacy protection: no email addresses in lead
        serialized = json.dumps(lead)
        self.assertNotIn("secret.sender@example.edu", serialized)
        self.assertNotIn("student.recipient@example.edu", serialized)

    def test_cross_source_dedup_lorax_and_geco_independent(self):
        lorax_event = {
            "id": "2330437",
            "title": "Lorax TGIF!",
            "url": "https://yaleconnect.yale.edu/rsvp_boot?id=2330437",
            "host": "Forestry Club (FC)",
            "location": "Private Location (sign in to display)",
            "record_kind": "food_candidate"
        }
        geco_lead = {
            "id": "user-email:geco-tgif:2026-09-18",
            "title": "GECO TGIF",
            "url": None,
            "club_url": "https://yaleconnect.yale.edu/geco/club_signup",
            "location": "Mann Student Center",
            "record_kind": "unverified_food_lead"
        }
        events, leads = fetch_free_food.merge_and_deduplicate_records([lorax_event], [], [], [geco_lead])
        self.assertEqual(len(events), 1)
        self.assertEqual(len(leads), 1)
        self.assertEqual(events[0]["id"], "2330437")
        self.assertEqual(leads[0]["id"], "user-email:geco-tgif:2026-09-18")

    def test_e2e_0918_cross_source_pipeline(self):
        out = StringIO()
        with patch.object(sys, "argv", [
            "fetch_free_food.py", "--date", "2026-09-18", "--fixtures-dir", FIXTURES_DIR, "--json"
        ]), patch("sys.stdout", out):
            with self.assertRaises(SystemExit) as cm:
                fetch_free_food.main()
            self.assertEqual(cm.exception.code, 0)

        data = json.loads(out.getvalue())
        self.assertIn("meta", data)
        self.assertIn("events", data)
        self.assertIn("unverified_food_leads", data)

        self.assertEqual(data["meta"]["target_date"], "2026-09-18")
        self.assertEqual(data["meta"]["coverage_status"], "partial")
        self.assertEqual(data["meta"]["inherited_candidates"], 6)
        self.assertEqual(data["meta"]["supplemental_food_candidates"], 2)
        self.assertEqual(data["meta"]["unverified_food_leads"], 1)
        self.assertTrue(data["meta"]["inherited_records_not_reverified"])

        # Exactly 8 candidates
        events = data["events"]
        self.assertEqual(len(events), 8)
        event_ids = [e["id"] for e in events]
        self.assertIn("2330295", event_ids)
        self.assertIn("2329688", event_ids)
        self.assertIn("2331237", event_ids)
        self.assertIn("2330437", event_ids)
        self.assertIn("2330205", event_ids)
        self.assertIn("2329433", event_ids)
        self.assertIn("windhamcampbell:2026:morning-wake-up-2", event_ids)
        self.assertIn("luma:pnay4247", event_ids)

        # Exactly 1 unverified food lead
        leads = data["unverified_food_leads"]
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0]["id"], "user-email:geco-tgif:2026-09-18")
        self.assertEqual(leads[0]["food_status"], "unclear")
        self.assertEqual(leads[0]["food_cost"], "unknown")

    def test_windham_discovery_variant_and_food_exclusion(self):
        # 1. Schedule discovery
        schedule_html = """
        <html><body>
          <a href="/festivals/2026/variant-afternoon-talk-9">Variant Talk</a>
        </body></html>
        """
        urls = fetch_free_food.discover_windham_campbell_urls(schedule_html)
        self.assertEqual(urls, ["https://windhamcampbell.org/festivals/2026/variant-afternoon-talk-9"])

        # 2. Detail with food
        detail_html = """
        <html><body>
          <h1>Variant Afternoon Talk</h1>
          <time datetime="2026-09-25 15:00"><span>Friday, September 25th</span><span>3:00 PM</span></time>
          <dt>Location</dt><dd><span>Beinecke Plaza</span></dd>
          <p>Join us for complimentary coffee and treats 2:30-3:00 PM before the discussion. Free and open to the public.</p>
        </body></html>
        """
        ev = fetch_free_food.parse_windham_campbell_page(detail_html, url=urls[0])
        self.assertIsNotNone(ev)
        self.assertEqual(ev["id"], "windhamcampbell:2026:variant-afternoon-talk-9")
        self.assertEqual(ev["title"], "Variant Afternoon Talk")
        self.assertEqual(ev["starts_at"], "2026-09-25T15:00:00-04:00")
        self.assertEqual(ev["food_service_start"], "2026-09-25T14:30:00-04:00")
        self.assertEqual(ev["food_service_end"], "2026-09-25T15:00:00-04:00")
        self.assertEqual(ev["location"], "Beinecke Plaza")
        self.assertEqual(ev["confidence"], "confirmed_free")
        self.assertEqual(ev["food_status"], "provided")

        # 3. Detail WITHOUT food
        no_food_html = """
        <html><body>
          <h1>Variant Afternoon Talk</h1>
          <time datetime="2026-09-25 15:00"><span>Friday, September 25th</span><span>3:00 PM</span></time>
          <dt>Location</dt><dd><span>Beinecke Plaza</span></dd>
          <p>A reading and discussion by prize recipients.</p>
        </body></html>
        """
        ev_no_food = fetch_free_food.parse_windham_campbell_page(no_food_html, url=urls[0])
        self.assertTrue(ev_no_food is None or ev_no_food.get("confidence") != "confirmed_free")

    def test_bioct_luma_discovery_variant_and_food_exclusion(self):
        # 1. Calendar discovery
        calendar_html = """
        <html><body>
          <a href="https://bioct.org/event/variant-bioct-gathering/">Variant BioCT Gathering</a>
        </body></html>
        """
        b_urls = fetch_free_food.discover_bioct_event_urls(calendar_html)
        self.assertEqual(b_urls, ["https://bioct.org/event/variant-bioct-gathering/"])

        # 2. BioCT detail cross-reference
        bioct_html = """
        <html><body>
          <h1 class="tribe-events-single-event-title">Variant BioCT Gathering</h1>
          <a href="https://luma.com/variant-luma-42">RSVP on Luma</a>
          <abbr class="tribe-events-abbr tribe-events-start-date published dtstart" title="2026-10-15">October 15</abbr>
          <span class="tribe-event-date-start">October 15 @ 6:00 pm - 9:00 pm</span>
        </body></html>
        """
        b_data = fetch_free_food.parse_bioct_page(bioct_html, url=b_urls[0])
        self.assertEqual(b_data["luma_url"], "https://luma.com/variant-luma-42")

        # 3. Luma detail with food
        luma_html = """
        <script type="application/ld+json">
        {
          "@type": "Event",
          "name": "Variant Luma Gathering",
          "startDate": "2026-10-15T18:00:00-04:00",
          "endDate": "2026-10-15T21:00:00-04:00",
          "location": {"name": "100 College St", "address": {"streetAddress": "100 College St"}}
        }
        </script>
        <p>Food and drinks will be provided for all attendees.</p>
        """
        l_ev = fetch_free_food.parse_luma_page(luma_html, url=b_data["luma_url"])
        self.assertIsNotNone(l_ev)
        self.assertEqual(l_ev["id"], "luma:variant-luma-42")
        self.assertEqual(l_ev["title"], "Variant Luma Gathering")
        self.assertEqual(l_ev["food_status"], "provided")
        self.assertEqual(l_ev["confidence"], "needs_verification")

        # 4. Luma detail WITHOUT food
        luma_no_food = """
        <script type="application/ld+json">
        {
          "@type": "Event",
          "name": "Variant Luma Gathering",
          "startDate": "2026-10-15T18:00:00-04:00",
          "endDate": "2026-10-15T21:00:00-04:00",
          "location": {"name": "100 College St"}
        }
        </script>
        <p>Annual general meeting. No food or refreshments provided.</p>
        """
        l_no_food = fetch_free_food.parse_luma_page(luma_no_food, url=b_data["luma_url"])
        self.assertTrue(l_no_food is None or l_no_food.get("food_status") != "provided")

    def test_main_two_links_distinct_content_and_no_stale_reuse(self):
        pages = {
            "https://windhamcampbell.org/festivals/2026": """
            <html><body>
              <a href="https://windhamcampbell.org/festivals/2026/morning-wake-up-with-the-yale-review-2">Morning</a>
              <a href="https://windhamcampbell.org/festivals/2026/afternoon-reading-no-food">Afternoon</a>
            </body></html>
            """,
            "https://windhamcampbell.org/festivals/2026/morning-wake-up-with-the-yale-review-2": """
            <html><body>
              <h1>Morning Wake Up</h1>
              <time datetime="2026-09-18 10:30">Friday, September 18th · 10:30 AM</time>
              <dt>Location</dt><dd><span>College Street Tent</span></dd>
              <p>Join us for complimentary coffee and treats 10:00-10:30 AM. Free and open to the public.</p>
            </body></html>
            """,
            "https://windhamcampbell.org/festivals/2026/afternoon-reading-no-food": """
            <html><body>
              <h1>Afternoon Reading</h1>
              <time datetime="2026-09-18 15:00">Friday, September 18th · 3:00 PM</time>
              <dt>Location</dt><dd><span>Yale University Art Gallery</span></dd>
              <p>Prize recipients present short readings. Free and open to the public.</p>
            </body></html>
            """,
            "https://bioct.org/events-calendar/": "<html></html>"
        }

        out = StringIO()
        with patch.object(sys, "argv", [
            "fetch_free_food.py", "--date", "2026-09-18", "--fetch-external", "--json"
        ]), patch("sys.stdout", out), patch.dict(fetch_free_food.main.__globals__, {
            "fetch_events": lambda **kw: [],
            "fetch_event_detail_html": lambda url, **kw: pages.get(url, "<html></html>")
        }):
            with self.assertRaises(SystemExit) as cm:
                fetch_free_food.main()
            self.assertEqual(cm.exception.code, 0)

        data = json.loads(out.getvalue())
        evs = data.get("events", [])

        # Link 1 is present with confirmed_free
        mornings = [e for e in evs if e["id"] == "windhamcampbell:2026:morning-wake-up-2"]
        self.assertEqual(len(mornings), 1)
        self.assertEqual(mornings[0]["title"], "Morning Wake Up")
        self.assertEqual(mornings[0]["confidence"], "confirmed_free")
        self.assertEqual(mornings[0]["food_status"], "provided")
        self.assertEqual(mornings[0]["food_cost"], "free")
        self.assertEqual(mornings[0]["starts_at"], "2026-09-18T10:30:00-04:00")

        # Link 2 had NO food, so it is strictly NOT included as a food candidate
        afternoons = [e for e in evs if "afternoon" in e.get("id", "").lower()]
        self.assertEqual(len(afternoons), 0)

    def test_fixture_resolution_isolates_unmapped_urls(self):
        # Passing an unmapped URL returns None from resolve_fixture_file
        res = fetch_free_food.resolve_fixture_file(FIXTURES_DIR, "windham", "https://windhamcampbell.org/festivals/2026/completely-unmapped-talk")
        self.assertIsNone(res)

        # Passing the mapped URL returns the exact fixture
        res_mapped = fetch_free_food.resolve_fixture_file(FIXTURES_DIR, "windham", "https://windhamcampbell.org/festivals/2026/morning-wake-up-with-the-yale-review-2")
        self.assertIsNotNone(res_mapped)
        self.assertTrue(res_mapped.name.endswith("fixture-windham.html"))

    def test_regression_welcome_back_picnic_food_cost_paid(self):
        item = {
            "p1": "2331213",
            "p3": "Welcome Back Picnic",
            "p4": "Sat, Sep 19, 2026 2 PM – 4:30 PM",
            "p5": "Social",
            "p6": "304 St Ronan St (by the Div School)",
            "p9": "YSN Black Nurses Association",
            "p12": "Free",
            "p18": "/rsvp_boot?id=2331213",
            "p22": ""
        }
        detail = {
            "details_text": "Black Graduate Network PICNIC! Food Provided (Soul Food Plates $7 Preorder $9 At event )",
            "has_food_badge": True,
            "admission_price": "free"
        }
        r = fetch_free_food.classify_event(item, detail_info=detail, upcoming_only=False)
        self.assertIsNotNone(r)
        self.assertEqual(r["food_cost"], "paid")
        self.assertEqual(r["confidence"], "excluded")
        self.assertTrue(any("预订$7／现场$9" in n for n in r["food_cost_notes"]))

    def test_regression_elena_ice_cream_paid_by_housing(self):
        item = {
            "p1": "2326835",
            "p3": "Elena's Ice Cream Trip",
            "p4": "Mon, Sep 14, 2026 7 PM – 8 PM",
            "p5": "Social",
            "p6": "Private Location",
            "p9": "Divinity Housing",
            "p12": "Free",
            "p18": "/rsvp_boot?id=2326835",
            "p22": "Social"
        }
        detail = {
            "details_text": "Join us for a walk to Elena's for an ice cream treat (paid for by Yale housing)! Elena's has a wonderful selection.",
            "has_food_badge": True,
            "admission_price": "free"
        }
        r = fetch_free_food.classify_event(item, detail_info=detail, upcoming_only=False)
        self.assertIsNotNone(r)
        self.assertEqual(r["food_cost"], "free")
        self.assertEqual(r["confidence"], "confirmed_free")
        self.assertTrue(any("paid for by Yale housing" in n for n in r["food_cost_notes"]))
        self.assertTrue(any("Yale housing" in b for b in r.get("benefit_notes", [])))

    def test_probe_room_rental_paid_by_housing_food_cost_unknown(self):
        item = {
            "p1": "probe_room",
            "p3": "Community Lunch",
            "p4": "Mon, Sep 14, 2026 12 PM – 1 PM",
            "p5": "Activity",
            "p6": "Campus",
            "p9": "Club",
            "p12": "Free",
            "p18": "/rsvp?id=991",
            "p22": ""
        }
        detail = {
            "details_text": "Lunch provided. Room rental (paid for by Yale housing).",
            "has_food_badge": True,
            "admission_price": "free"
        }
        r = fetch_free_food.classify_event(item, detail_info=detail, upcoming_only=False)
        self.assertIsNotNone(r)
        self.assertEqual(r["food_status"], "provided")
        self.assertEqual(r["food_cost"], "unknown")
        self.assertEqual(r["confidence"], "needs_verification")
        self.assertFalse(any("paid for by Yale housing" in n for n in r["food_cost_notes"]))

    def test_probe_ice_cream_paid_by_attendees_not_free(self):
        item = {
            "p1": "probe_attendee",
            "p3": "Community Lunch",
            "p4": "Mon, Sep 14, 2026 12 PM – 1 PM",
            "p5": "Activity",
            "p6": "Campus",
            "p9": "Club",
            "p12": "Free",
            "p18": "/rsvp?id=992",
            "p22": ""
        }
        detail = {
            "details_text": "Lunch provided. Ice cream treat (paid for by attendees).",
            "has_food_badge": True,
            "admission_price": "free"
        }
        r = fetch_free_food.classify_event(item, detail_info=detail, upcoming_only=False)
        self.assertIsNotNone(r)
        self.assertEqual(r["food_cost"], "paid")
        self.assertEqual(r["confidence"], "excluded")
        self.assertTrue(any("paid for by attendees" in n for n in r["food_cost_notes"]))

    def test_probe_plates_zero_dollars_not_paid(self):
        item = {
            "p1": "probe_zero",
            "p3": "Community Lunch",
            "p4": "Mon, Sep 14, 2026 12 PM – 1 PM",
            "p5": "Activity",
            "p6": "Campus",
            "p9": "Club",
            "p12": "Free",
            "p18": "/rsvp?id=993",
            "p22": ""
        }
        detail = {
            "details_text": "Food Provided. Plates $0",
            "has_food_badge": True,
            "admission_price": "free"
        }
        r = fetch_free_food.classify_event(item, detail_info=detail, upcoming_only=False)
        self.assertIsNotNone(r)
        self.assertNotEqual(r["food_cost"], "paid")
        self.assertNotEqual(r["confidence"], "excluded")
        self.assertEqual(r["food_cost"], "unknown")
        self.assertEqual(r["confidence"], "needs_verification")

    def test_probe_host_treat_oiss_free_food(self):
        item = {
            "p1": "2330677",
            "p3": "ISPY Meet-Up: Explore Farmers' Market Together!",
            "p4": "Sat, Sep 26, 2026 9:30 AM – 11 AM",
            "p5": "Social",
            "p6": "Private Location",
            "p9": "Office of International Students & Scholars",
            "p12": "Free",
            "p18": "/rsvp?id=2330677",
            "p22": "Food"
        }
        detail = {
            "details_text": "Please gather at 9:30am outside the entrance of Conte West Hills Magnet School (511 Chapel Street) for check-in before heading to the adjacent farmers' market together. OISS will treat you to a delicious snack from one of the local stalls at the Market!",
            "has_food_badge": False,
            "admission_price": "free",
            "benefit_notes": ["OISS请参加者吃一份市场摊位小吃；不代表全部购物免费或无限量"],
            "eligibility": "面向Yale学生及学者的配偶/伴侣社群；其他人能否参加未明示",
            "rsvp_status": "公开页面提供免费RSVP；每人最多2张票",
            "meeting_notes": "9:30在Conte West Hills Magnet School入口外集合，511 Chapel Street"
        }
        r = fetch_free_food.classify_event(item, detail_info=detail, upcoming_only=False)
        self.assertIsNotNone(r)
        self.assertEqual(r["food_cost"], "free")
        self.assertEqual(r["confidence"], "confirmed_free")
        self.assertEqual(r["location"], "9:30在Conte West Hills Magnet School入口外集合，511 Chapel Street")
        self.assertEqual(r["rsvp_status"], "公开页面提供免费RSVP；每人最多2张票")
        self.assertTrue(any("OISS请参加者吃一份市场摊位小吃" in b for b in r["benefit_notes"]))

    def test_probe_treat_yourself_not_host_treat(self):
        item = {
            "p1": "probe_yourself",
            "p3": "Farmers Market Tour",
            "p4": "Sat, Sep 26, 2026 10 AM – 12 PM",
            "p5": "Social",
            "p6": "Market",
            "p9": "Club",
            "p12": "Free",
            "p18": "/rsvp?id=994",
            "p22": ""
        }
        detail = {
            "details_text": "Come explore the market and treat yourself to a snack from the local stalls!",
            "has_food_badge": False,
            "admission_price": "free"
        }
        r = fetch_free_food.classify_event(item, detail_info=detail, upcoming_only=False)
        self.assertIsNotNone(r)
        self.assertNotEqual(r["food_cost"], "free")
        self.assertEqual(r["confidence"], "needs_verification")
        self.assertEqual(r["benefit_notes"], [])

    def test_probe_registration_closed_alert(self):
        html = """
        <div id="more_tickets">
            <h2>Registration</h2>
            <div class="alert alert-info">
                Registration will only be open from <b>Jan 1, 0001 (at 1 AM)</b> to <b>Sep 20, 2026 (at 11:30 PM)</b>.
                It's now 8:12 AM on Sep 21, 2026.
            </div>
        </div>
        <div id="event_details">
            <h2>Details</h2>
            Come appreciate yourself and your fellow postdocs at this end of summer BBQ! Food and drinks will be provided
            <span class="mdi mdi-food"></span> Food Provided (BBQ and drinks)
        </div>
        """
        d = fetch_free_food.parse_detail_page(html)
        self.assertEqual(d["registration_status"], "closed")
        self.assertEqual(d["registration_deadline"], "2026-09-20T23:30:00-04:00")
        self.assertEqual(d["rsvp_status"], "报名已截止（页面截止时间2026-09-20 23:30 EDT）")
        self.assertEqual(d["eligibility"], "正文邀请博士后；其他人员准入unknown")

    def test_probe_registration_sales_end_deadline(self):
        html = """
        <div id="more_tickets">
            <h2>Registration</h2>
            <table>
                <tr><th>Options</th><th>Sales End</th><th>Price</th></tr>
                <tr><td>RSVP</td><td>Sep 23, 2026 at 5 PM</td><td>FREE</td></tr>
            </table>
        </div>
        <div id="event_details">
            <h2>Details</h2>
            Lunch Provided / RSVP Required. Email the Schell Center to request background readings a week prior to talk.
        </div>
        """
        d = fetch_free_food.parse_detail_page(html)
        self.assertEqual(d["registration_deadline"], "2026-09-23T17:00:00-04:00")
        self.assertEqual(d["rsvp_status"], "必须RSVP；报名截止2026-09-23 17:00 EDT")

    def test_probe_gather_instruction_vs_casual_gathering(self):
        text = "Join OISS for a casual gathering at the Farmers' market near Wooster Square! Please gather at 9:30am outside the entrance of Conte West Hills Magnet School (511 Chapel Street) for check-in before heading to the market."
        html = f"<div id='event_details'><h2>Details</h2>{text}</div>"
        d = fetch_free_food.parse_detail_page(html)
        self.assertEqual(d["meeting_notes"], "9:30在Conte West Hills Magnet School入口外集合，511 Chapel Street")

    def test_discover_oiss_calendar_events(self):
        html = """
        <div class="views-row">
            <span class="views-field views-field-title">
                <span class="field-content"><a href="/event/international-coffee-hour">International Coffee Hour</a></span>
            </span>
            <span class="views-field views-field-field-event-date">
                <span class="date-display-single" content="2026-09-24T15:30:00-04:00">Thu, 09/24/2026 - 3:30pm</span>
            </span>
        </div>
        <div class="views-row">
            <span class="views-field views-field-title">
                <span class="field-content"><a href="https://external.yale.edu/event/outside-range">Old Event</a></span>
            </span>
            <span class="views-field views-field-field-event-date">
                <span class="date-display-single" content="2026-08-10T10:00:00-04:00">Mon, 08/10/2026</span>
            </span>
        </div>
        """
        events = fetch_free_food.discover_oiss_calendar_events(html)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["title"], "International Coffee Hour")
        self.assertEqual(events[0]["url"], "https://oiss.yale.edu/event/international-coffee-hour")
        self.assertEqual(events[0]["starts_at"], "2026-09-24T15:30:00-04:00")
        self.assertEqual(events[1]["url"], "https://external.yale.edu/event/outside-range")

    def test_discover_tsai_city_events(self):
        html = """
        <div class="node-teaser">
            <h3 class="node-title"><a href="https://yaleconnect.yale.edu/event/2399999">Pitch Night & Dinner</a></h3>
            <div class="field-date">Tuesday, September 22, 2026 - 6:00pm</div>
        </div>
        <div class="node-teaser">
            <h3 class="node-title"><a href="https://lu.ma/tsai-city-mixer">Innovator Mixer</a></h3>
            <div class="field-date">Wednesday, September 23, 2026</div>
        </div>
        """
        events = fetch_free_food.discover_tsai_city_events(html)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["title"], "Pitch Night & Dinner")
        self.assertEqual(events[0]["url"], "https://yaleconnect.yale.edu/event/2399999")
        self.assertEqual(events[0]["target_type"], "yaleconnect")
        self.assertEqual(events[0]["date_str"], "2026-09-22")
        self.assertEqual(events[1]["title"], "Innovator Mixer")
        self.assertEqual(events[1]["url"], "https://lu.ma/tsai-city-mixer")
        self.assertEqual(events[1]["target_type"], "luma")

    def test_ysm_calendar_discovery_and_filtering(self):
        # 1. Test calendar list parsing
        list_html = """
        <article class="event-list-item">
            <a href="/events/a-ysph-symposium-on-gwas-and-prs">
                <h3>A YSPH Symposium on GWAS and PRS</h3>
            </a>
            <div class="event-date" aria-label="September 24, 2026 8:00 AM to 5:00 PM">Sep 24</div>
        </article>
        <article class="event-list-item">
            <a href="https://medicine.yale.edu/events/rheumatology-rounds">
                <h3>Rheumatology Rounds</h3>
            </a>
            <div class="event-date" aria-label="September 25, 2026 8:00 AM to 9:00 AM">Sep 25</div>
        </article>
        """
        events = fetch_free_food.discover_ysm_calendar_events(list_html)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["title"], "A YSPH Symposium on GWAS and PRS")
        self.assertEqual(events[0]["url"], "https://medicine.yale.edu/events/a-ysph-symposium-on-gwas-and-prs")
        self.assertEqual(events[0]["date_str"], "2026-09-24")

        # 2. Test page data parsing for valid GWAS symposium
        gwas_page = """
        <html><body>
        <script id="page-data" type="application/json">
        {
            "title": "A YSPH Symposium on GWAS and PRS",
            "startDate": "2026-09-24T08:00:00-04:00",
            "endDate": "2026-09-24T17:00:00-04:00",
            "eventLocation": "Winslow Auditorium, 60 College St, New Haven, CT",
            "locationType": "In-Person",
            "description": "Join us for the GWAS Symposium. Breakfast (8:00-8:30) and lunch (12:00-1:00) provided.",
            "foodDetails": "Breakfast and Lunch",
            "cost": "Free",
            "registrationUrl": "https://yaleconnect.yale.edu/rsvp_boot?id=2330888",
            "openTo": "Yale Community"
        }
        </script>
        </body></html>
        """
        parsed_gwas = fetch_free_food.parse_ysm_event_page(gwas_page, "https://medicine.yale.edu/events/gwas")
        self.assertIsNotNone(parsed_gwas)
        self.assertEqual(parsed_gwas["title"], "A YSPH Symposium on GWAS and PRS")
        self.assertEqual(parsed_gwas["food_status"], "provided")
        self.assertEqual(parsed_gwas["food_cost"], "unknown")
        self.assertEqual(parsed_gwas["admission_cost"], "free")
        self.assertEqual(parsed_gwas["confidence"], "needs_verification")
        self.assertEqual(parsed_gwas["evidence"], ["YSM official foodDetails: Breakfast and Lunch"])

        # 3. Test page data parsing for Cancelled event
        cancelled_page = """
        <html><body>
        <script id="page-data" type="application/json">
        {
            "title": "Cancelled: Genetics Seminar",
            "status": "Cancelled",
            "startDate": "2026-09-24T12:00:00-04:00",
            "foodDetails": "Lunch provided"
        }
        </script>
        </body></html>
        """
        self.assertIsNone(fetch_free_food.parse_ysm_event_page(cancelled_page, "https://medicine.yale.edu/events/genetics"))

        # 4. Test page data parsing for coffee-only event (retained as beverage, not dropped)
        coffee_page = """
        <html><body>
        <script id="page-data" type="application/json">
        {
            "title": "Rheumatology Grand Rounds",
            "status": "Live",
            "startDate": "2026-09-25T08:00:00-04:00",
            "eventLocation": "Hope 110, New Haven",
            "description": "Weekly rounds with case discussion.",
            "foodDetails": "Coffee"
        }
        </script>
        </body></html>
        """
        parsed_coffee = fetch_free_food.parse_ysm_event_page(coffee_page, "https://medicine.yale.edu/events/rounds")
        self.assertIsNotNone(parsed_coffee)
        self.assertEqual(parsed_coffee["food_status"], "provided")
        self.assertEqual(parsed_coffee["benefit_type"], "beverage")
        self.assertEqual(parsed_coffee["confidence"], "needs_verification")

    def test_windham_festival_window_outside(self):
        html = """
        <div class="festival-dates">
            <h2>Windham-Campbell Festival: 2026-09-15 to 2026-09-18</h2>
        </div>
        """
        res = fetch_free_food.check_windham_festival_window(html, range_from="2026-09-21", range_to="2026-09-27")
        self.assertTrue(res["outside_window"])
        self.assertEqual(res["fest_dates"], ["2026-09-15", "2026-09-18"])

        res_during = fetch_free_food.check_windham_festival_window(html, range_from="2026-09-17", range_to="2026-09-19")
        self.assertFalse(res_during["outside_window"])

    def test_multisource_global_budget_enforcement(self):
        fetch_free_food.ExternalSourceFetcher.set_global_budget(3)
        mock_fn = MagicMock(return_value="<html>ok</html>")
        fetcher = fetch_free_food.ExternalSourceFetcher("ysm_calendar", budget=10, fetch_fn=mock_fn)
        r1 = fetcher.fetch("https://medicine.yale.edu/1")
        r2 = fetcher.fetch("https://medicine.yale.edu/2")
        r3 = fetcher.fetch("https://medicine.yale.edu/3")
        r4 = fetcher.fetch("https://medicine.yale.edu/4")
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)
        self.assertIsNotNone(r3)
        self.assertIsNone(r4)
        self.assertEqual(fetcher.requests_made, 3)
        self.assertTrue(fetcher.exhausted)
        self.assertEqual(mock_fn.call_count, 3)

    def test_merge_and_deduplicate_records_preserves_discovered_from(self):
        primary = [{
            "id": "ysm:gwas",
            "url": "https://medicine.yale.edu/events/gwas",
            "title": "GWAS Symposium",
            "starts_at": "2026-09-24T08:00:00-04:00",
            "source_type": "yaleconnect",
            "discovered_from": ["yaleconnect"]
        }]
        incoming = [{
            "id": "ysm:gwas",
            "url": "https://medicine.yale.edu/events/gwas",
            "title": "GWAS Symposium",
            "starts_at": "2026-09-24T08:00:00-04:00",
            "source_type": "ysm_calendar",
            "discovered_from": ["ysm_calendar"]
        }]
        merged, _ = fetch_free_food.merge_and_deduplicate_records(primary, incoming)
        self.assertEqual(len(merged), 1)
        self.assertIn("yaleconnect", merged[0]["discovered_from"])
        self.assertIn("ysm_calendar", merged[0]["discovered_from"])

    def test_probe_ysm_lunch_no_cost_unknown_cost(self):
        page = """<html><body><script id="page-data" type="application/json">{
            "title": "Review probe",
            "foodDetails": "Lunch",
            "eventLocation": {"city": "New Haven"},
            "startDate": "2026-09-24T12:00:00-04:00"
        }</script></body></html>"""
        res = fetch_free_food.parse_ysm_event_page(page, "https://medicine.yale.edu/events/probe1")
        self.assertIsNotNone(res)
        self.assertEqual(res["food_status"], "provided")
        self.assertEqual(res["food_cost"], "unknown")
        self.assertEqual(res["admission_cost"], "unknown")
        self.assertEqual(res["confidence"], "needs_verification")

    def test_probe_ysm_coffee_beverage_retained(self):
        page = """<html><body><script id="page-data" type="application/json">{
            "title": "Grand Rounds",
            "foodDetails": "Coffee",
            "eventLocation": {"city": "New Haven"},
            "startDate": "2026-09-24T08:00:00-04:00"
        }</script></body></html>"""
        res = fetch_free_food.parse_ysm_event_page(page, "https://medicine.yale.edu/events/coffee")
        self.assertIsNotNone(res)
        self.assertEqual(res["food_status"], "provided")
        self.assertEqual(res["benefit_type"], "beverage")
        self.assertEqual(res["confidence"], "needs_verification")

    def test_probe_ysm_complimentary_ice_cream_free(self):
        page = """<html><body><script id="page-data" type="application/json">{
            "title": "Ice Cream Social",
            "foodDetails": "Complimentary ice cream",
            "eventLocation": {"city": "New Haven"},
            "startDate": "2026-09-24T14:00:00-04:00"
        }</script></body></html>"""
        res = fetch_free_food.parse_ysm_event_page(page, "https://medicine.yale.edu/events/icecream")
        self.assertIsNotNone(res)
        self.assertEqual(res["food_status"], "provided")
        self.assertEqual(res["food_cost"], "free")
        self.assertEqual(res["confidence"], "confirmed_free")

    def test_probe_ysm_free_admission_lunch_unknown_food_cost(self):
        page = """<html><body><script id="page-data" type="application/json">{
            "title": "Symposium",
            "cost": "Free",
            "foodDetails": "Lunch",
            "eventLocation": {"city": "New Haven"},
            "startDate": "2026-09-24T12:00:00-04:00"
        }</script></body></html>"""
        res = fetch_free_food.parse_ysm_event_page(page, "https://medicine.yale.edu/events/free-lunch")
        self.assertIsNotNone(res)
        self.assertEqual(res["food_status"], "provided")
        self.assertEqual(res["admission_cost"], "free")
        self.assertEqual(res["food_cost"], "unknown")
        self.assertEqual(res["confidence"], "needs_verification")

    def test_probe_ysm_paid_admission_complimentary_snack_excluded(self):
        page = """<html><body><script id="page-data" type="application/json">{
            "title": "Paid Workshop",
            "cost": "$25.00",
            "foodDetails": "Complimentary snack",
            "eventLocation": {"city": "New Haven"},
            "startDate": "2026-09-24T12:00:00-04:00"
        }</script></body></html>"""
        res = fetch_free_food.parse_ysm_event_page(page, "https://medicine.yale.edu/events/paid-workshop")
        self.assertIsNotNone(res)
        self.assertEqual(res["food_status"], "provided")
        self.assertEqual(res["food_cost"], "free")
        self.assertEqual(res["admission_cost"], "paid ($25.00)")
        self.assertEqual(res["confidence"], "excluded")

    def test_probe_sacred_spaces_natural_classification_and_facts(self):
        html_text = """
        <html><body>
        <h1 class="rsvp__event-name">Tour of Yale&#39;s Sacred Spaces - Fall 2026</h1>
        <div class="rsvp__event-price">FREE</div>
        <h2>Registration</h2>
        <p>This event is open to specific members only. RSVP Limit: 2 tickets per user. FREE.</p>
        <h2>Details</h2>
        <p>[This program is open to all OISS community members, regardless of belief system]</p>
        <p>We are partnering with the Chaplain's Office to take you on a tour to Yale's beautiful sacred spaces. Join us for a 2-hour walking tour where we will visit:</p>
        <ul>
          <li>Slifka Center for Jewish Life</li>
          <li>Battell Chapel (built in 1870s)</li>
          <li>Chaplain's Office (with complimentary ice cream, including dairy-free options)</li>
        </ul>
        <div class="venue-address">Private Location (register to display)</div>
        </body></html>
        """
        d_info = fetch_free_food.parse_detail_page(html_text)
        self.assertEqual(d_info["admission_price"], "free")
        self.assertIn("不含乳制品选项", d_info["benefit_notes"][0])
        self.assertIn("正文明确面向所有 OISS community members（不限信仰）", d_info["eligibility"])
        self.assertIn("注册区提示特定成员限定需登录核实", d_info["eligibility"])

        res = fetch_free_food.classify_event({
            "p1": "2329944",
            "p3": "Tour of Yale's Sacred Spaces - Fall 2026",
            "p4": "Thu, Sep 24, 2026 3:15 PM - 4:45 PM",
            "p6": "Private Location (register to display)",
            "p18": "https://yaleconnect.yale.edu/OISS/rsvp?id=2329944"
        }, detail_info=d_info)
        self.assertIsNotNone(res)
        self.assertEqual(res["food_status"], "provided")
        self.assertEqual(res["food_cost"], "free")
        self.assertEqual(res["admission_cost"], "free")
        self.assertEqual(res["confidence"], "confirmed_free")
        self.assertEqual(res["location"], "Private Location (register to display)")

    # =========================================================================
    # 27. Discovery Upgrade: Profile Tuning, Store Priority, Coverage Aggregation
    # =========================================================================
    def test_coverage_aggregation_partial_external(self):
        """Verify that when an external source has partial coverage, global coverage_status is partial."""
        feed = []
        globals_ = fetch_free_food.main.__globals__
        out = StringIO()
        with patch.dict(globals_, {
            "fetch_events": lambda **kw: feed,
            "fetch_event_detail_html": lambda *a, **kw: "<html><body><a href='https://luma.com/ev1'>1</a><a href='https://luma.com/ev2'>2</a></body></html>",
            "extract_tsai_city_events": lambda *a, **kw: [("https://luma.com/ev1", "luma"), ("https://luma.com/ev2", "luma")],
        }), patch.object(fetch_free_food.ExternalSourceFetcher, "can_fetch", return_value=False), patch.object(sys, "argv", [
            "fetch_free_food.py", "--date", "2026-09-27", "--json", "--multisource", "--sources", "tsai_city"
        ]), patch("sys.stdout", out):
            with self.assertRaises(SystemExit) as cm:
                fetch_free_food.main()
            self.assertEqual(cm.exception.code, 0)

        meta = json.loads(out.getvalue())["meta"]
        self.assertEqual(meta["coverage_status"], "partial")

    def test_profile_and_script_sha256_in_meta(self):
        """Verify that --profile and script_sha256 are present in meta."""
        globals_ = fetch_free_food.main.__globals__
        out = StringIO()
        with patch.dict(globals_, {
            "fetch_events": lambda **kw: [],
        }), patch.object(sys, "argv", [
            "fetch_free_food.py", "--date", "2026-09-27", "--json", "--profile", "quick"
        ]), patch("sys.stdout", out):
            with self.assertRaises(SystemExit) as cm:
                fetch_free_food.main()
            self.assertEqual(cm.exception.code, 0)

        meta = json.loads(out.getvalue())["meta"]
        self.assertEqual(meta["profile"], "quick")
        self.assertIn("script_sha256", meta)
        self.assertNotEqual(meta["script_sha256"], "unknown")
        self.assertIn("store", meta)


if __name__ == "__main__":
    unittest.main(verbosity=2)
