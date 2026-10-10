#!/usr/bin/env python3
"""
test_candidate.py
Automated regression test suite for Household Electronics Management (HEM) candidate.
Verifies validation strictness, status reporting integrity, maintenance recording safety,
and YAML parser correctness.
"""

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import inventory_io as io

BASE_FIXTURE = """category: kitchen_appliances
devices:
  - id: TEST-001
    brand: Test
    model: Fixture
    name: Synthetic Device
    serial_number: SYNTHETIC-SN-001
    status: active
    purchase_price_usd: 10.00
    warranty_expiry: "2026-11-01"
    maintenance_schedule:
      wash:
        interval_days: 30
        last_serviced: "2026-10-01"
        next_due: "2026-10-31"
"""


class HEMBaseTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="hem-test-")
        self.root = Path(self.temp_dir.name)
        self.scripts = self.root / "scripts"
        self.inv = self.root / "inventory"
        self.logs = self.root / "maintenance_logs"
        self.scripts.mkdir()
        self.inv.mkdir()
        self.logs.mkdir()

        for p in SCRIPTS_DIR.glob("*.py"):
            shutil.copy2(p, self.scripts / p.name)

        self.fixture_file = self.inv / "fixture.yaml"
        self.fixture_file.write_text(BASE_FIXTURE, encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_script(self, script_name, *args):
        cmd = [sys.executable, "-B", str(self.scripts / script_name), *args]
        return subprocess.run(cmd, capture_output=True, text=True)


class TestInventoryValidation(HEMBaseTestCase):
    def test_negative_price_rejected(self):
        self.fixture_file.write_text(BASE_FIXTURE.replace("10.00", "-5.00"), encoding="utf-8")
        res = self.run_script("validate_inventory.py", "--inventory-dir", str(self.inv))
        self.assertEqual(res.returncode, 1)
        self.assertIn("negative purchase price", res.stderr)

    def test_nonnumeric_price_rejected(self):
        self.fixture_file.write_text(BASE_FIXTURE.replace("10.00", "not-a-number"), encoding="utf-8")
        res = self.run_script("validate_inventory.py", "--inventory-dir", str(self.inv))
        self.assertEqual(res.returncode, 1)
        self.assertIn("non-numeric purchase price", res.stderr)

    def test_price_nan_and_inf_rejected_without_traceback(self):
        for val in ["NaN", "Infinity", "-Infinity"]:
            self.fixture_file.write_text(BASE_FIXTURE.replace("10.00", val), encoding="utf-8")
            res = self.run_script("validate_inventory.py", "--inventory-dir", str(self.inv))
            self.assertEqual(res.returncode, 1, f"Should reject {val}")
            self.assertNotIn("Traceback", res.stderr)

    def test_interval_fraction_and_boolean_rejected_without_traceback(self):
        for val in ["1.5", "true", "false"]:
            self.fixture_file.write_text(BASE_FIXTURE.replace("interval_days: 30", f"interval_days: {val}"), encoding="utf-8")
            res = self.run_script("validate_inventory.py", "--inventory-dir", str(self.inv))
            self.assertEqual(res.returncode, 1, f"Should reject interval_days: {val}")
            self.assertNotIn("Traceback", res.stderr)

    def test_null_devices_rejected_cleanly(self):
        self.fixture_file.write_text("category: kitchen_appliances\ndevices: null\n", encoding="utf-8")
        res = self.run_script("validate_inventory.py", "--inventory-dir", str(self.inv))
        self.assertEqual(res.returncode, 1)
        self.assertIn("'devices' must be a list", res.stderr)
        self.assertNotIn("Traceback", res.stderr)

    def test_duplicate_device_id_rejected(self):
        dup_entry = BASE_FIXTURE[BASE_FIXTURE.index("  - id:"):]
        self.fixture_file.write_text(BASE_FIXTURE + dup_entry, encoding="utf-8")
        res = self.run_script("validate_inventory.py", "--inventory-dir", str(self.inv))
        self.assertEqual(res.returncode, 1)
        self.assertIn("Duplicate device ID", res.stderr)

    def test_missing_inventory_directory_handled_without_traceback(self):
        res = self.run_script("validate_inventory.py", "--inventory-dir", str(self.root / "nonexistent_dir"))
        self.assertEqual(res.returncode, 1)
        self.assertNotIn("Traceback", res.stderr)

    def test_candidate_inventory_passes_validation(self):
        res = self.run_script("validate_inventory.py", "--inventory-dir", str(PROJECT_ROOT / "inventory"), "--as-of", "2026-10-08")
        self.assertEqual(res.returncode, 0)
        self.assertIn("ALL INVENTORY CHECKS PASSED", res.stdout)


class TestStatusReport(HEMBaseTestCase):
    def test_status_report_fails_fast_on_negative_price(self):
        self.fixture_file.write_text(BASE_FIXTURE.replace("10.00", "-15.00"), encoding="utf-8")
        res = self.run_script("status_report.py", "--inventory-dir", str(self.inv), "--as-of", "2026-10-08", "--json")
        self.assertEqual(res.returncode, 1)
        self.assertIn("negative purchase price", res.stderr)

    def test_status_report_fails_fast_on_duplicate_id(self):
        dup_entry = BASE_FIXTURE[BASE_FIXTURE.index("  - id:"):]
        self.fixture_file.write_text(BASE_FIXTURE + dup_entry, encoding="utf-8")
        res = self.run_script("status_report.py", "--inventory-dir", str(self.inv), "--as-of", "2026-10-08", "--json")
        self.assertEqual(res.returncode, 1)
        self.assertIn("Duplicate device ID", res.stderr)

    def test_example_record_segregated_from_held_assets(self):
        example_text = BASE_FIXTURE.replace("    status: active", "    record_kind: example\n    status: active")
        self.fixture_file.write_text(example_text, encoding="utf-8")
        res = self.run_script("status_report.py", "--inventory-dir", str(self.inv), "--as-of", "2026-10-08", "--json")
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        cat_stats = data["categories"]["kitchen_appliances"]
        self.assertEqual(cat_stats["held_count"], 0)
        self.assertEqual(cat_stats["held_value"], 0.0)
        self.assertEqual(cat_stats["example_count"], 1)
        self.assertEqual(cat_stats["example_value"], 10.0)

    def test_candidate_status_report_json(self):
        res = self.run_script("status_report.py", "--inventory-dir", str(PROJECT_ROOT / "inventory"), "--as-of", "2026-10-08", "--json")
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data["total_devices"], 10)
        self.assertEqual(data["real_devices_count"], 10)
        self.assertEqual(data["example_devices_count"], 0)
        self.assertEqual(data["warranty"]["in_warranty_count"], 2)
        self.assertEqual(data["warranty"]["expiring_soon_subset_count"], 1)
        self.assertEqual(data["warranty"]["expired_count"], 8)
        self.assertEqual(data["warranty"]["unknown_count"], 0)
        self.assertEqual(len(data["warranty"]["expiring_soon_items"]), 1)
        self.assertEqual(data["warranty"]["expiring_soon_items"][0]["days_remaining"], 33)


class TestMaintenanceRecording(HEMBaseTestCase):
    def test_writer_does_not_invent_checkmarks(self):
        res = self.run_script(
            "record_maintenance.py",
            "--device", "TEST-001",
            "--task", "wash",
            "--date", "2026-10-08",
            "--inventory-dir", str(self.inv),
            "--logs-dir", str(self.logs)
        )
        self.assertEqual(res.returncode, 0)
        log_files = list(self.logs.glob("*.md"))
        self.assertEqual(len(log_files), 1)
        content = log_files[0].read_text(encoding="utf-8")
        self.assertIn("- [ ] 操作流程已按官方手册要求执行完毕", content)
        self.assertIn("- [ ] 设备功能测试正常", content)
        self.assertNotIn("[x]", content)

    def test_writer_idempotent_on_identical_rerun(self):
        args = [
            "record_maintenance.py",
            "--device", "TEST-001",
            "--task", "wash",
            "--date", "2026-10-08",
            "--inventory-dir", str(self.inv),
            "--logs-dir", str(self.logs)
        ]
        res1 = self.run_script(*args)
        self.assertEqual(res1.returncode, 0)
        log_path = next(self.logs.glob("*.md"))
        original_bytes = log_path.read_bytes()

        res2 = self.run_script(*args)
        self.assertEqual(res2.returncode, 0)
        self.assertEqual(log_path.read_bytes(), original_bytes)
        self.assertIn("idempotent", res2.stdout)

    def test_writer_fails_on_conflict(self):
        args = [
            "record_maintenance.py",
            "--device", "TEST-001",
            "--task", "wash",
            "--date", "2026-10-08",
            "--inventory-dir", str(self.inv),
            "--logs-dir", str(self.logs)
        ]
        res1 = self.run_script(*args)
        self.assertEqual(res1.returncode, 0)

        conflict_args = args + ["--cost", "88.00", "--notes", "Discrepancy cost and note"]
        res2 = self.run_script(*conflict_args)
        self.assertEqual(res2.returncode, 1)
        self.assertIn("Conflict detected", res2.stderr)

    def test_writer_rejects_future_date(self):
        res = self.run_script(
            "record_maintenance.py",
            "--device", "TEST-001",
            "--task", "wash",
            "--date", "2030-01-01",
            "--inventory-dir", str(self.inv),
            "--logs-dir", str(self.logs)
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("cannot be in the future", res.stderr)
        parsed = io.load_yaml(self.fixture_file)
        self.assertEqual(parsed["devices"][0]["maintenance_schedule"]["wash"]["last_serviced"], "2026-10-01")

    def test_writer_backdated_entry_does_not_regress_schedule(self):
        res = self.run_script(
            "record_maintenance.py",
            "--device", "TEST-001",
            "--task", "wash",
            "--date", "2026-09-01",
            "--inventory-dir", str(self.inv),
            "--logs-dir", str(self.logs)
        )
        self.assertEqual(res.returncode, 0)
        parsed = io.load_yaml(self.fixture_file)
        # Existing next_due was 2026-10-31; should NOT regress to 2026-10-01
        self.assertEqual(parsed["devices"][0]["maintenance_schedule"]["wash"]["next_due"], "2026-10-31")

    def test_writer_inserts_missing_last_serviced_atomically(self):
        no_ls_fixture = BASE_FIXTURE.replace('        last_serviced: "2026-10-01"\n', '')
        self.fixture_file.write_text(no_ls_fixture, encoding="utf-8")
        res = self.run_script(
            "record_maintenance.py",
            "--device", "TEST-001",
            "--task", "wash",
            "--date", "2026-10-08",
            "--inventory-dir", str(self.inv),
            "--logs-dir", str(self.logs)
        )
        self.assertEqual(res.returncode, 0)
        parsed = io.load_yaml(self.fixture_file)
        task = parsed["devices"][0]["maintenance_schedule"]["wash"]
        self.assertEqual(task["last_serviced"], "2026-10-08")
        self.assertEqual(task["next_due"], "2026-11-07")

    def test_writer_rejects_path_traversal_device_id(self):
        res = self.run_script(
            "record_maintenance.py",
            "--device", "../escaped",
            "--task", "wash",
            "--date", "2026-10-08",
            "--inventory-dir", str(self.inv),
            "--logs-dir", str(self.logs)
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("Invalid device ID", res.stderr)
        self.assertFalse((self.root / "escaped_20261008_wash.md").exists())


class TestYAMLParserEdgeCases(HEMBaseTestCase):
    def test_inline_list_quoted_comma(self):
        test_file = self.root / "inline_comma.yaml"
        test_file.write_text('targets: ["one, two", three]\n', encoding="utf-8")
        data = io.parse_simple_yaml(test_file)
        self.assertEqual(data["targets"], ["one, two", "three"])

    def test_unclosed_quote_raises_value_error(self):
        test_file = self.root / "unclosed.yaml"
        test_file.write_text('category: "unterminated\n', encoding="utf-8")
        with self.assertRaises(ValueError):
            io.parse_simple_yaml(test_file)

    def test_empty_list_parsed_properly(self):
        test_file = self.root / "empty.yaml"
        test_file.write_text("category: test\ndevices: []\n", encoding="utf-8")
        data = io.parse_simple_yaml(test_file)
        self.assertEqual(data["category"], "test")
        self.assertEqual(data["devices"], [])


class TestRound3AcceptanceAThroughF(HEMBaseTestCase):
    def test_acceptance_a_demo_zero_modification_and_real_runs_independently(self):
        before_bytes = self.fixture_file.read_bytes()
        # 1. Run with --demo
        res_demo = self.run_script(
            "record_maintenance.py",
            "--device", "TEST-001",
            "--task", "wash",
            "--date", "2026-10-08",
            "--demo",
            "--inventory-dir", str(self.inv),
            "--logs-dir", str(self.logs)
        )
        self.assertEqual(res_demo.returncode, 0)
        self.assertEqual(self.fixture_file.read_bytes(), before_bytes, "Demo run must not modify real inventory YAML")
        demo_logs = list(self.logs.glob("demo_*.md"))
        self.assertEqual(len(demo_logs), 1)
        self.assertIn("record_kind: example", demo_logs[0].read_text(encoding="utf-8"))

        # 2. Run real entry on the same date
        res_real = self.run_script(
            "record_maintenance.py",
            "--device", "TEST-001",
            "--task", "wash",
            "--date", "2026-10-08",
            "--inventory-dir", str(self.inv),
            "--logs-dir", str(self.logs)
        )
        self.assertEqual(res_real.returncode, 0)
        real_logs = list(self.logs.glob("TEST-001_*.md"))
        self.assertEqual(len(real_logs), 1)
        parsed = io.load_yaml(self.fixture_file)
        self.assertEqual(parsed["devices"][0]["maintenance_schedule"]["wash"]["last_serviced"], "2026-10-08")
        self.assertEqual(parsed["devices"][0]["maintenance_schedule"]["wash"]["next_due"], "2026-11-07")

    def test_acceptance_b_fractional_interval_rejected_across_all_entrypoints(self):
        bad_fixture = BASE_FIXTURE.replace("interval_days: 30", "interval_days: 1.5")
        self.fixture_file.write_text(bad_fixture, encoding="utf-8")
        before_bytes = self.fixture_file.read_bytes()

        res_val = self.run_script("validate_inventory.py", "--inventory-dir", str(self.inv), "--as-of", "2026-10-08")
        self.assertEqual(res_val.returncode, 1)

        res_rep = self.run_script("status_report.py", "--inventory-dir", str(self.inv), "--as-of", "2026-10-08", "--json")
        self.assertEqual(res_rep.returncode, 1)

        res_wri = self.run_script(
            "record_maintenance.py",
            "--device", "TEST-001",
            "--task", "wash",
            "--date", "2026-10-08",
            "--inventory-dir", str(self.inv),
            "--logs-dir", str(self.logs)
        )
        self.assertEqual(res_wri.returncode, 1)
        self.assertEqual(self.fixture_file.read_bytes(), before_bytes, "No side effects on invalid interval")
        self.assertEqual(len(list(self.logs.glob("*.md"))), 0)

    def test_acceptance_c_reordered_fields_real_update(self):
        reordered_fixture = BASE_FIXTURE.replace(
            "  - id: TEST-001\n    brand: Test",
            "  - brand: Test\n    id: TEST-001"
        )
        self.fixture_file.write_text(reordered_fixture, encoding="utf-8")

        res = self.run_script(
            "record_maintenance.py",
            "--device", "TEST-001",
            "--task", "wash",
            "--date", "2026-10-08",
            "--inventory-dir", str(self.inv),
            "--logs-dir", str(self.logs)
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("UPDATED:", res.stdout)

        parsed = io.load_yaml(self.fixture_file)
        task = parsed["devices"][0]["maintenance_schedule"]["wash"]
        self.assertEqual(task["last_serviced"], "2026-10-08")
        self.assertEqual(task["next_due"], "2026-11-07")

    def test_acceptance_d_retry_after_failed_commit_completes_update(self):
        date_compact = "20261008"
        log_name = f"TEST-001_{date_compact}_wash.md"
        log_file = self.logs / log_name
        log_content = """# 维护与耗材更换记录单 (Maintenance Log)

---
log_id: "MAINT-20261008-TEST-001-wash"
device_id: "TEST-001"
brand: "Test"
model: "Fixture"
task: "wash"
date: "2026-10-08"
operator: "User"
cost_usd: null
interval_days: 30
next_due: "2026-11-07"
record_kind: real
---

## 1. 维护操作摘要
- **执行项目**: wash
- **操作人员**: User
- **记录备注**: 日常按周期常规维护

## 2. 检查指标 (待用户实测核实，不自动预填完成)
- [ ] 操作流程已按官方手册要求执行完毕
- [ ] 设备功能测试正常
- [ ] 下次维护周期已推算至 2026-11-07
"""
        log_file.write_text(log_content, encoding="utf-8")

        # The YAML file still has the old next_due: 2026-10-31
        parsed_before = io.load_yaml(self.fixture_file)
        self.assertEqual(parsed_before["devices"][0]["maintenance_schedule"]["wash"]["next_due"], "2026-10-31")

        # Retry should complete the YAML update
        res = self.run_script(
            "record_maintenance.py",
            "--device", "TEST-001",
            "--task", "wash",
            "--date", "2026-10-08",
            "--inventory-dir", str(self.inv),
            "--logs-dir", str(self.logs)
        )
        self.assertEqual(res.returncode, 0)
        self.assertNotIn("idempotent", res.stdout.lower())
        self.assertIn("Completing inventory schedule update", res.stdout)

        parsed_after = io.load_yaml(self.fixture_file)
        self.assertEqual(parsed_after["devices"][0]["maintenance_schedule"]["wash"]["next_due"], "2026-11-07")

        # Third run is now idempotent
        res3 = self.run_script(
            "record_maintenance.py",
            "--device", "TEST-001",
            "--task", "wash",
            "--date", "2026-10-08",
            "--inventory-dir", str(self.inv),
            "--logs-dir", str(self.logs)
        )
        self.assertEqual(res3.returncode, 0)
        self.assertIn("idempotent", res3.stdout.lower())

    def test_acceptance_e_example_excluded_from_real_alerts_and_invalid_enum(self):
        example_fixture = BASE_FIXTURE.replace(
            "    status: active",
            "    record_kind: example\n    status: active"
        ).replace('next_due: "2026-10-31"', 'next_due: "2026-10-09"')
        self.fixture_file.write_text(example_fixture, encoding="utf-8")

        res = self.run_script("status_report.py", "--inventory-dir", str(self.inv), "--as-of", "2026-10-08", "--json")
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(len(data["maintenance_alerts"]), 0)
        self.assertEqual(data["warranty"]["in_warranty_count"], 0)

        # Invalid enum rejected
        typo_fixture = BASE_FIXTURE.replace("    status: active", "    record_kind: typo\n    status: active")
        self.fixture_file.write_text(typo_fixture, encoding="utf-8")
        res_val = self.run_script("validate_inventory.py", "--inventory-dir", str(self.inv))
        self.assertEqual(res_val.returncode, 1)
        self.assertIn("invalid record_kind", res_val.stderr)

    def test_acceptance_f_unverified_registered_cost_and_missing_price(self):
        no_price_fixture = BASE_FIXTURE.replace("    purchase_price_usd: 10.00\n", "")
        self.fixture_file.write_text(no_price_fixture, encoding="utf-8")

        res = self.run_script("status_report.py", "--inventory-dir", str(self.inv), "--as-of", "2026-10-08", "--json")
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        cat_data = data["categories"]["kitchen_appliances"]
        self.assertEqual(cat_data["unknown_price_count"], 1)
        self.assertEqual(cat_data["known_price_count"], 0)
        self.assertEqual(cat_data["registered_cost"], 0.0)

        res_cli = self.run_script("status_report.py", "--inventory-dir", str(self.inv), "--as-of", "2026-10-08")
        self.assertEqual(res_cli.returncode, 0)
        self.assertIn("未核实设备登记", res_cli.stdout)
        self.assertIn("缺价登记: 1 件", res_cli.stdout)

    def test_acceptance_g_mixed_verification_status_counting(self):
        mixed_fixture = """category: computing_and_storage
devices:
  - id: DEV-COMP-001
    brand: BrandA
    model: ModelA
    name: Dev1
    record_kind: real
    verification_status: verified
    source_refs: ["invoice_receipt_pdf"]
    status: active
    purchase_price_usd: 100.00
    warranty_expiry: "2026-11-01"
  - id: DEV-COMP-002
    brand: BrandB
    model: ModelB
    name: Dev2
    record_kind: real
    verification_status: unverified
    status: active
    purchase_price_usd: 50.00
    warranty_expiry: "2026-11-01"
"""
        self.fixture_file.write_text(mixed_fixture, encoding="utf-8")
        res_json = self.run_script("status_report.py", "--inventory-dir", str(self.inv), "--as-of", "2026-10-08", "--json")
        self.assertEqual(res_json.returncode, 0)
        data = json.loads(res_json.stdout)
        self.assertEqual(data["real_devices_count"], 2)
        self.assertEqual(data["verified_registered_devices_count"], 1)
        self.assertEqual(data["unverified_registered_devices_count"], 1)

        res_cli = self.run_script("status_report.py", "--inventory-dir", str(self.inv), "--as-of", "2026-10-08")
        self.assertEqual(res_cli.returncode, 0)
        self.assertIn("已核实 1 件，未核实 1 件", res_cli.stdout)

    def test_acceptance_h_nested_cloud_sync_policy_displayed(self):
        comp_fixture = """category: computing_and_storage
devices:
  - id: DEV-COMP-001
    brand: Apple
    model: MacBook Pro
    name: Laptop
    record_kind: real
    verification_status: unverified
    status: active
    purchase_price_usd: 1999.00
    warranty_expiry: "2026-11-01"
    data_hygiene:
      filevault_enabled: true
      cloud_sync: "iCloud Drive (Local Root First)"
      last_backup_verified: "2026-10-01"
"""
        self.fixture_file.write_text(comp_fixture, encoding="utf-8")
        res_cli = self.run_script("status_report.py", "--inventory-dir", str(self.inv), "--as-of", "2026-10-08")
        self.assertEqual(res_cli.returncode, 0)
        self.assertIn("云端同步策略: iCloud Drive (Local Root First)", res_cli.stdout)
        self.assertNotIn("云端同步策略: 未配置", res_cli.stdout)

    def test_acceptance_i_cost_semantics_omitted_explicit_zero_and_conflict(self):
        # Scenario 1: Omitted cost records 'cost_usd: null'
        res1 = self.run_script(
            "record_maintenance.py",
            "--device", "TEST-001",
            "--task", "wash",
            "--date", "2026-10-08",
            "--inventory-dir", str(self.inv),
            "--logs-dir", str(self.logs)
        )
        self.assertEqual(res1.returncode, 0)
        log1 = self.logs / "TEST-001_20261008_wash.md"
        self.assertIn("cost_usd: null", log1.read_text(encoding="utf-8"))

        # Scenario 2: Identical rerun with omitted cost is idempotent
        res2 = self.run_script(
            "record_maintenance.py",
            "--device", "TEST-001",
            "--task", "wash",
            "--date", "2026-10-08",
            "--inventory-dir", str(self.inv),
            "--logs-dir", str(self.logs)
        )
        self.assertEqual(res2.returncode, 0)
        self.assertIn("idempotent", res2.stdout.lower())

        # Scenario 3: Subsequent rerun with explicit cost detects conflict and does not silently overwrite
        res3 = self.run_script(
            "record_maintenance.py",
            "--device", "TEST-001",
            "--task", "wash",
            "--date", "2026-10-08",
            "--cost", "15.00",
            "--inventory-dir", str(self.inv),
            "--logs-dir", str(self.logs)
        )
        self.assertEqual(res3.returncode, 1)
        self.assertIn("Conflict detected", res3.stderr)

        # Scenario 4: Explicit --cost 0 records 'cost_usd: 0.00' and is idempotent on rerun
        log1.unlink()
        res4 = self.run_script(
            "record_maintenance.py",
            "--device", "TEST-001",
            "--task", "wash",
            "--date", "2026-10-08",
            "--cost", "0",
            "--inventory-dir", str(self.inv),
            "--logs-dir", str(self.logs)
        )
        self.assertEqual(res4.returncode, 0)
        self.assertIn("cost_usd: 0.00", log1.read_text(encoding="utf-8"))

        res5 = self.run_script(
            "record_maintenance.py",
            "--device", "TEST-001",
            "--task", "wash",
            "--date", "2026-10-08",
            "--cost", "0.00",
            "--inventory-dir", str(self.inv),
            "--logs-dir", str(self.logs)
        )
        self.assertEqual(res5.returncode, 0)
        self.assertIn("idempotent", res5.stdout.lower())

        # Scenario 5: Omitted cost on existing cost=0.00 log detects conflict
        res6 = self.run_script(
            "record_maintenance.py",
            "--device", "TEST-001",
            "--task", "wash",
            "--date", "2026-10-08",
            "--inventory-dir", str(self.inv),
            "--logs-dir", str(self.logs)
        )
        self.assertEqual(res6.returncode, 1)
        self.assertIn("Conflict detected", res6.stderr)


if __name__ == "__main__":
    unittest.main()

