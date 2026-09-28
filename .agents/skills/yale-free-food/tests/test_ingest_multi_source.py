#!/usr/bin/env python3
"""
Comprehensive Unit & Integration Test Suite for Yale Multi-Source Event Ingestion Hub
=====================================================================================
Covers Astra's 8 Acceptance Verification Categories:
  1. Security & TLS enforcement, failure handling without store writes.
  2. Classification Contract: 5D orthogonality (paid tickets, free meal, unclear, error pages).
  3. WeChat robustness: Chinese datetime parsing, no default hallucinations, stable IDs.
  4. Email boundary: invoice filtering, Message-ID deduplication, undated leads isolation.
  5. Timezone & DST: ZoneInfo, autumn repeat hour, spring gap hour.
  6. Storage reliability: priority hierarchy, corruption protection, atomic flocked writes.
  7. End-to-end 3-path integration: matching date, mismatch date, undated lead preservation.
  8. Regression & source audit de-faking verification.
"""

import sys
import os
import io
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Zoneinfo import
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
FIXTURES_DIR = SKILL_ROOT / "tests" / "fixtures"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ingest_multi_source
from ingest_multi_source import (
    NetworkFetchError,
    StorageCorruptedError,
    InvalidEventError,
    get_ssl_context,
    resolve_store_path,
    load_supplement_store,
    save_supplement_store,
    upsert_event,
    evaluate_food_classification,
    parse_luma_html,
    parse_eventbrite_html,
    parse_wechat_notice,
    parse_email_text,
    get_ny_timezone
)
import fetch_free_food


class TestSecurityAndNetwork(unittest.TestCase):
    """1. Security & TLS enforcement, failure handling without store writes."""

    def test_get_ssl_context_enforces_strict_verification(self):
        ctx = get_ssl_context()
        self.assertIsNotNone(ctx)
        # Must require certificate verification
        import ssl
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    @patch("urllib.request.urlopen")
    def test_network_fetch_failure_raises_and_preserves_store(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        with self.assertRaises(NetworkFetchError):
            ingest_multi_source.fetch_url_html("https://luma.com/fail-event")

        # Test CLI exits non-zero and does not modify store
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_store = Path(tmpdir) / "store.json"
            initial_data = {"events": [], "unverified_food_leads": []}
            save_supplement_store(initial_data, temp_store)

            with patch.object(sys, "argv", [
                "ingest_multi_source.py", "--luma", "https://luma.com/fail-event", "--store", str(temp_store)
            ]):
                with self.assertRaises(SystemExit) as cm:
                    ingest_multi_source.main()
                self.assertEqual(cm.exception.code, 1)

            # Store remains intact and empty
            loaded = load_supplement_store(temp_store)
            self.assertEqual(len(loaded["events"]), 0)


class TestClassificationContract(unittest.TestCase):
    """2. Classification Contract: 5D orthogonality and error page rejection."""

    def test_luma_paid_ticket_marked_excluded(self):
        html = """
        <html>
        <head>
          <script type="application/ld+json">
          {
            "@type": "Event",
            "name": "Yale AI Founders Dinner",
            "description": "Exclusive networking dinner with catered Italian food. Dinner and wine included.",
            "startDate": "2026-10-10T18:00:00-04:00",
            "endDate": "2026-10-10T21:00:00-04:00",
            "location": {"name": "Graduate New Haven"},
            "offers": {"@type": "Offer", "price": "45.00", "priceCurrency": "USD"}
          }
          </script>
        </head>
        <body></body>
        </html>
        """
        rec = parse_luma_html(html, url="https://luma.com/ai-dinner")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["admission_cost"], "paid ($45.00)")
        self.assertEqual(rec["confidence"], "excluded")
        self.assertEqual(rec["food_status"], "provided")

    def test_luma_free_admission_unclear_food(self):
        html = """
        <html>
        <head>
          <script type="application/ld+json">
          {
            "@type": "Event",
            "name": "Yale AI Social Mixer",
            "description": "Informal social mixer to connect students and researchers.",
            "startDate": "2026-10-10T18:00:00-04:00",
            "location": {"name": "Bass Library Lounge"},
            "offers": {"@type": "Offer", "price": 0}
          }
          </script>
        </head>
        <body></body>
        </html>
        """
        rec = parse_luma_html(html, url="https://luma.com/ai-mixer")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["admission_cost"], "free")
        self.assertEqual(rec["food_status"], "likely")  # mixer
        self.assertEqual(rec["food_cost"], "unknown")
        self.assertEqual(rec["confidence"], "needs_verification")

    def test_luma_explicit_free_food(self):
        html = """
        <html>
        <head>
          <script type="application/ld+json">
          {
            "@type": "Event",
            "name": "Yale Hacker Meetup",
            "description": "Free pizza and boba provided for everyone! Free admission.",
            "startDate": "2026-10-10T18:00:00-04:00",
            "location": {"name": "17 Hillhouse Ave"}
          }
          </script>
        </head>
        <body></body>
        </html>
        """
        rec = parse_luma_html(html, url="https://luma.com/hacker-meetup")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["food_status"], "provided")
        self.assertEqual(rec["food_cost"], "free")
        self.assertEqual(rec["confidence"], "confirmed_free")

    def test_non_food_page_returns_none(self):
        html = """
        <html>
        <head>
          <script type="application/ld+json">
          {
            "@type": "Event",
            "name": "Linear Algebra Study Session",
            "description": "Review session for midterm exam. Solving problem sets.",
            "startDate": "2026-10-10T18:00:00-04:00"
          }
          </script>
        </head>
        <body></body>
        </html>
        """
        rec = parse_luma_html(html, url="https://luma.com/math-study")
        self.assertIsNone(rec)

    def test_error_pages_rejected(self):
        err_luma = "<html><head><title>503 Service Unavailable</title></head><body><h1>503 Service Unavailable</h1></body></html>"
        with self.assertRaises(InvalidEventError):
            parse_luma_html(err_luma, url="https://luma.com/bad")

        err_eb = "<html><head><title>404 Page Not Found</title></head><body><h1>404 Error</h1></body></html>"
        with self.assertRaises(InvalidEventError):
            parse_eventbrite_html(err_eb, url="https://eventbrite.com/tickets-999")


class TestWeChatParser(unittest.TestCase):
    """3. WeChat parser robustness: Chinese datetime, no default hallucinations, stable IDs."""

    def test_chinese_date_and_time_recognition(self):
        text = """
        耶鲁秋季创投沙龙
        主办：耶鲁创投俱乐部 (YVC)
        时间：2026年10月15日 下午2点到4点
        地点：Dunbar Hall 201
        现场提供精美茶歇与披萨，欢迎踊跃报名！
        报名链接：https://forms.gle/sample1
        """
        rec = parse_wechat_notice(text)
        self.assertIsNotNone(rec)
        self.assertIn("10-15T14:00:00", rec["starts_at"])
        self.assertIn("10-15T16:00:00", rec["ends_at"])
        self.assertEqual(rec["location"], "Dunbar Hall 201")
        self.assertEqual(rec["food_status"], "provided")

    def test_wechat_missing_year_preserved_as_unverified_lead(self):
        text = "讲座\n10月15日 下午2点到4点\n提供披萨"
        rec = parse_wechat_notice(text)
        self.assertIsNotNone(rec)
        self.assertIsNone(rec["starts_at"])
        self.assertIsNone(rec["url"])
        self.assertEqual(rec["record_kind"], "unverified_food_lead")

    def test_missing_fields_no_hallucinations(self):
        text = """
        耶鲁前沿论坛
        主办：学联
        本周活动设有交流酒会
        详情敬请期待后续通知
        """
        rec = parse_wechat_notice(text)
        self.assertIsNotNone(rec)
        # Must not fabricate Sep 27 or LC 102
        self.assertIsNone(rec["starts_at"])
        self.assertNotIn("Linsly-Chittenden", rec["location"])
        self.assertEqual(rec["record_kind"], "unverified_food_lead")

    def test_different_chinese_titles_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_store = Path(tmpdir) / "store.json"
            save_supplement_store({"events": [], "unverified_food_leads": []}, temp_store)

            notice_a = "耶鲁创投之夜 2026\n时间：2026年10月20日 19:00-21:00\n包含专场交流酒会\n地点：LC 102"
            notice_b = "耶鲁生科中秋联谊\n时间：2026年10月21日 18:00-20:00\n包含迎新酒会\n地点：YSB"

            rec_a = parse_wechat_notice(notice_a, store_path=temp_store)
            rec_b = parse_wechat_notice(notice_b, store_path=temp_store)

            self.assertNotEqual(rec_a["id"], rec_b["id"])
            loaded = load_supplement_store(temp_store)
            self.assertEqual(len(loaded["events"]), 2)

    def test_idempotent_upsert(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_store = Path(tmpdir) / "store.json"
            save_supplement_store({"events": [], "unverified_food_leads": []}, temp_store)

            notice = "耶鲁机器人沙龙\n时间：2026年10月25日 14:00-16:00\n提供免费披萨"
            rec1 = parse_wechat_notice(notice, store_path=temp_store)
            rec2 = parse_wechat_notice(notice, store_path=temp_store)

            self.assertEqual(rec1["id"], rec2["id"])
            loaded = load_supplement_store(temp_store)
            self.assertEqual(len(loaded["events"]), 1)


class TestEmailParser(unittest.TestCase):
    """4. Email parser boundaries: invoice filtering, Message-ID deduplication, undated leads."""

    def test_invoice_receipt_emails_filtered_out(self):
        invoice = """
        From: billing@yale.edu
        Subject: Invoice #10294 - Yale Catering Services
        Date: 20 Sep 2026 10:00:00 -0400

        Your receipt for order #10294. Total payment received: $150.00.
        """
        rec = parse_email_text(invoice)
        self.assertIsNone(rec)

    def test_distinct_message_ids_no_collision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_store = Path(tmpdir) / "store.json"
            save_supplement_store({"events": [], "unverified_food_leads": []}, temp_store)

            email_1 = """Message-ID: <tgif-week1@yale.edu>
Subject: CS TGIF
Date: 18 Sep 2026 14:00:00 -0400

Join us for TGIF this Friday at 5 PM! Pizza provided in Watson.
"""
            email_2 = """Message-ID: <tgif-week2@yale.edu>
Subject: CS TGIF
Date: 25 Sep 2026 14:00:00 -0400

Join us for TGIF this Friday at 5 PM! Pizza provided in Watson.
"""
            rec_1 = parse_email_text(email_1, store_path=temp_store)
            rec_2 = parse_email_text(email_2, store_path=temp_store)

            self.assertNotEqual(rec_1["id"], rec_2["id"])
            loaded = load_supplement_store(temp_store)
            self.assertEqual(len(loaded["events"]), 2)

    def test_undated_email_lead_isolated(self):
        email_text = """
        From: grad-lounge@yale.edu
        Subject: Bagels and Coffee in Student Lounge
        
        Hey everyone, we have fresh bagels and coffee available in the 3rd floor lounge today!
        """
        rec = parse_email_text(email_text)
        self.assertIsNotNone(rec)
        self.assertIsNone(rec["starts_at"])
        self.assertEqual(rec["record_kind"], "unverified_food_lead")


class TestTimezoneAndDST(unittest.TestCase):
    """5. Timezone & DST transitions: ZoneInfo, autumn repeat hour, spring gap hour."""

    def test_ny_timezone_resolution(self):
        tz = get_ny_timezone()
        self.assertIsNotNone(tz)

    def test_dst_autumn_repeat_hour(self):
        # 2026-11-01 01:30 AM is ambiguous (fall back from EDT to EST)
        if ZoneInfo:
            tz = ZoneInfo("America/New_York")
            dt_edt = datetime(2026, 11, 1, 1, 30, fold=0, tzinfo=tz)
            dt_est = datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=tz)
            self.assertEqual(dt_edt.strftime("%z"), "-0400")
            self.assertEqual(dt_est.strftime("%z"), "-0500")

    def test_dst_spring_gap_hour(self):
        # 2026-03-08 02:30 AM does not exist in local time (springs forward from 2 AM to 3 AM)
        if ZoneInfo:
            tz = ZoneInfo("America/New_York")
            dt_spring = datetime(2026, 3, 8, 2, 30, tzinfo=tz)
            # Must not crash and resolve to valid UTC offset
            self.assertIn(dt_spring.strftime("%z"), ["-0500", "-0400"])


class TestStorageReliability(unittest.TestCase):
    """6. Storage reliability: priority hierarchy, corruption protection, atomic writes."""

    def test_storage_priority_order(self):
        # 1. CLI explicit store
        p1 = resolve_store_path("/tmp/cli_store.json")
        self.assertEqual(p1, Path("/tmp/cli_store.json").resolve())

        # 2. Env variable
        with patch.dict(os.environ, {"YALE_FREE_FOOD_STORE": "/tmp/env_store.json"}):
            p2 = resolve_store_path(None)
            self.assertEqual(p2, Path("/tmp/env_store.json").resolve())

        # 3. Default user directory
        with patch.dict(os.environ, {}, clear=True):
            p3 = resolve_store_path(None)
            self.assertTrue(str(p3).endswith(".yale-free-food/supplemental_events.json"))

    def test_corrupted_json_raises_storage_corrupted_error_and_preserves_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_file = Path(tmpdir) / "corrupted.json"
            bad_content = '{"events": [ {"id": "1", "title": incomplete ...'
            bad_file.write_text(bad_content, encoding="utf-8")

            with self.assertRaises(StorageCorruptedError):
                load_supplement_store(bad_file)

            # Assert original file was not wiped or overwritten
            self.assertEqual(bad_file.read_text(encoding="utf-8"), bad_content)

            # CLI with corrupted store exits 1
            with patch.object(sys, "argv", ["ingest_multi_source.py", "--list", "--store", str(bad_file)]):
                with self.assertRaises(SystemExit) as cm:
                    ingest_multi_source.main()
                self.assertEqual(cm.exception.code, 1)

    def test_atomic_replacement_and_clean_temp_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = Path(tmpdir) / "atomic_store.json"
            data = {"events": [{"id": "ev1", "title": "Test Event"}], "unverified_food_leads": []}
            save_supplement_store(data, store_file)

            # File must exist and contain valid data
            loaded = load_supplement_store(store_file)
            self.assertEqual(len(loaded["events"]), 1)

            # No temporary files left over
            tmp_files = list(Path(tmpdir).glob(".supp_store_tmp_*"))
            self.assertEqual(len(tmp_files), 0)


class TestEndToEndThreePaths(unittest.TestCase):
    """7. End-to-end 3-path integration with fetch_free_food."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store_file = Path(self.tmpdir.name) / "test_supplement.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_path_1_date_matches_included_and_paid_filtered(self):
        # Store has 1 free event and 1 paid event on 2026-09-27
        store_content = {
            "events": [
                {
                    "id": "luma:free-hackathon",
                    "title": "Yale Free Hackathon",
                    "starts_at": "2026-09-27T10:00:00-04:00",
                    "ends_at": "2026-09-27T18:00:00-04:00",
                    "location": "17 Hillhouse",
                    "food_status": "provided",
                    "food_cost": "free",
                    "admission_cost": "free",
                    "confidence": "confirmed_free",
                    "record_kind": "food_candidate"
                },
                {
                    "id": "luma:paid-gala",
                    "title": "Yale Paid Charity Gala",
                    "starts_at": "2026-09-27T19:00:00-04:00",
                    "ends_at": "2026-09-27T22:00:00-04:00",
                    "location": "Omni Hotel",
                    "food_status": "provided",
                    "food_cost": "paid",
                    "admission_cost": "paid ($50.00)",
                    "confidence": "excluded",
                    "record_kind": "food_candidate"
                }
            ],
            "unverified_food_leads": []
        }
        self.store_file.write_text(json.dumps(store_content), encoding="utf-8")

        # Without --include-paid: free hackathon is included, paid gala is excluded
        out = io.StringIO()
        with patch.object(sys, "argv", [
            "fetch_free_food.py", "--date", "2026-09-27", "--fixtures-dir", str(FIXTURES_DIR),
            "--supplement", str(self.store_file), "--json"
        ]), patch("sys.stdout", out):
            with self.assertRaises(SystemExit) as cm:
                fetch_free_food.main()
            self.assertEqual(cm.exception.code, 0)

        data = json.loads(out.getvalue())
        event_ids = [e["id"] for e in data["events"]]
        self.assertIn("luma:free-hackathon", event_ids)
        self.assertNotIn("luma:paid-gala", event_ids)

        # With --include-paid: paid gala is also included
        out_paid = io.StringIO()
        with patch.object(sys, "argv", [
            "fetch_free_food.py", "--date", "2026-09-27", "--fixtures-dir", str(FIXTURES_DIR),
            "--supplement", str(self.store_file), "--include-paid", "--json"
        ]), patch("sys.stdout", out_paid):
            with self.assertRaises(SystemExit) as cm:
                fetch_free_food.main()
            self.assertEqual(cm.exception.code, 0)

        data_paid = json.loads(out_paid.getvalue())
        paid_ids = [e["id"] for e in data_paid["events"]]
        self.assertIn("luma:free-hackathon", paid_ids)
        self.assertIn("luma:paid-gala", paid_ids)

    def test_path_2_date_mismatch_excluded(self):
        # Event is on 2026-09-27; query for 2026-09-18
        store_content = {
            "events": [
                {
                    "id": "luma:free-hackathon",
                    "title": "Yale Free Hackathon",
                    "starts_at": "2026-09-27T10:00:00-04:00",
                    "location": "17 Hillhouse",
                    "food_status": "provided",
                    "food_cost": "free",
                    "confidence": "confirmed_free",
                    "record_kind": "food_candidate"
                }
            ],
            "unverified_food_leads": []
        }
        self.store_file.write_text(json.dumps(store_content), encoding="utf-8")

        out = io.StringIO()
        with patch.object(sys, "argv", [
            "fetch_free_food.py", "--date", "2026-09-18", "--fixtures-dir", str(FIXTURES_DIR),
            "--supplement", str(self.store_file), "--json"
        ]), patch("sys.stdout", out):
            with self.assertRaises(SystemExit) as cm:
                fetch_free_food.main()
            self.assertEqual(cm.exception.code, 0)

        data = json.loads(out.getvalue())
        event_ids = [e["id"] for e in data["events"]]
        self.assertNotIn("luma:free-hackathon", event_ids)

    def test_path_3_undated_lead_preserved_in_unverified_leads_not_in_candidates(self):
        # Lead has food_status: provided, but starts_at is None
        store_content = {
            "events": [],
            "unverified_food_leads": [
                {
                    "id": "email:spontaneous-donuts",
                    "title": "Donuts in Math Lounge",
                    "starts_at": None,
                    "food_status": "provided",
                    "food_cost": "free",
                    "confidence": "confirmed_free",
                    "record_kind": "unverified_food_lead"
                }
            ]
        }
        self.store_file.write_text(json.dumps(store_content), encoding="utf-8")

        out = io.StringIO()
        with patch.object(sys, "argv", [
            "fetch_free_food.py", "--date", "2026-09-27", "--fixtures-dir", str(FIXTURES_DIR),
            "--supplement", str(self.store_file), "--json"
        ]), patch("sys.stdout", out):
            with self.assertRaises(SystemExit) as cm:
                fetch_free_food.main()
            self.assertEqual(cm.exception.code, 0)

        data = json.loads(out.getvalue())
        # Undated lead must NOT be in active events list
        event_ids = [e["id"] for e in data["events"]]
        self.assertNotIn("email:spontaneous-donuts", event_ids)

        # Undated lead MUST be preserved in unverified_food_leads
        lead_ids = [ld["id"] for ld in data["unverified_food_leads"]]
        self.assertIn("email:spontaneous-donuts", lead_ids)


class TestRegression(unittest.TestCase):
    """8. Regression & source audit de-faking verification."""

    def test_no_fake_crawler_status_from_supplement_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = Path(tmpdir) / "supplement.json"
            store_content = {
                "events": [
                    {
                        "id": "windhamcampbell:fake-reading",
                        "title": "Fake Windham Reading",
                        "starts_at": "2026-09-18T10:00:00-04:00",
                        "food_status": "provided",
                        "food_cost": "free",
                        "confidence": "confirmed_free"
                    },
                    {
                        "id": "bioct:fake-mixer",
                        "title": "Fake BioCT Mixer",
                        "starts_at": "2026-09-18T11:00:00-04:00",
                        "food_status": "provided",
                        "food_cost": "free",
                        "confidence": "confirmed_free"
                    }
                ],
                "unverified_food_leads": []
            }
            store_file.write_text(json.dumps(store_content), encoding="utf-8")

            # Isolate fixtures dir so windham/bioct fixture files do not trigger fixture runs
            isolated_fix_dir = Path(tmpdir) / "fixtures"
            isolated_fix_dir.mkdir()

            out = io.StringIO()
            with patch.object(sys, "argv", [
                "fetch_free_food.py", "--date", "2026-09-18", "--fixtures-dir", str(isolated_fix_dir),
                "--supplement", str(store_file), "--json"
            ]), patch("sys.stdout", out):
                with self.assertRaises(SystemExit) as cm:
                    fetch_free_food.main()
                self.assertEqual(cm.exception.code, 0)

            data = json.loads(out.getvalue())
            sources = {s["id"]: s for s in data["meta"]["sources"]}

            # With supplement store items having IDs windhamcampbell:..., bioct:...,
            # verify that none of these external crawlers are falsely set to 'checked'!
            self.assertEqual(sources["windham_campbell"]["run_status"], "not_checked")
            self.assertEqual(sources["bioct_luma"]["run_status"], "not_checked")


if __name__ == "__main__":
    unittest.main()
