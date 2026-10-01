#!/usr/bin/env python3
"""
test_init_project.py — Unit test suite for init_project.py (Multi-Agent OS v1.0.1)
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
import subprocess

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
MULTI_AGENT_OS_DIR = os.path.dirname(TESTS_DIR)
INIT_SCRIPT = os.path.join(MULTI_AGENT_OS_DIR, "tools", "init_project.py")


class TestInitProject(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="agent-os-init-test-")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_dry_run_does_not_create_files(self):
        target = os.path.join(self.test_dir, "dry_run_target")
        proc = subprocess.run([sys.executable, INIT_SCRIPT, "new", target, "--dry-run"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(os.path.exists(target))

    def test_new_scaffold_creates_all_files_and_manifest(self):
        target = os.path.join(self.test_dir, "new_project")
        proc = subprocess.run([sys.executable, INIT_SCRIPT, "new", target], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)

        expected_files = [
            "PROJECT.md",
            "AGENT_PROTOCOL.md",
            "AGENTS.md",
            "control/tasks/T-000-template.yaml",
            "control/tasks/README.md",
            ".agent-os-manifest.json"
        ]
        for rel_path in expected_files:
            full_path = os.path.join(target, rel_path)
            self.assertTrue(os.path.isfile(full_path), f"Expected file not found: {rel_path}")

        # Check manifest
        with open(os.path.join(target, ".agent-os-manifest.json"), "r") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["version"], "1.0.1")
        self.assertEqual(manifest["mode"], "new")
        self.assertIn("PROJECT.md", manifest["created_files"])

    def test_adopt_existing_agents_md_generates_merge_suggestion(self):
        target = os.path.join(self.test_dir, "existing_project")
        os.makedirs(target, exist_ok=True)
        existing_agents_content = "# Existing Custom Agent Config\nDo not overwrite me!\n"
        with open(os.path.join(target, "AGENTS.md"), "w") as f:
            f.write(existing_agents_content)

        proc = subprocess.run([sys.executable, INIT_SCRIPT, "adopt", target], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)

        # Ensure existing AGENTS.md was NOT overwritten
        with open(os.path.join(target, "AGENTS.md"), "r") as f:
            self.assertEqual(f.read(), existing_agents_content)

        # Ensure merge suggestion was generated
        suggestion_file = os.path.join(target, "AGENTS.md.merge-suggestion")
        self.assertTrue(os.path.isfile(suggestion_file))
        with open(suggestion_file, "r") as f:
            content = f.read()
        self.assertIn("Multi-Agent OS Integration", content)


if __name__ == "__main__":
    unittest.main()
