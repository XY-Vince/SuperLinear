#!/usr/bin/env python3
"""
test_worktree_review.py — Unit test suite for worktree_review.sh (Multi-Agent OS v1.0.1)
"""

import os
import sys
import shutil
import tempfile
import unittest
import subprocess

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
MULTI_AGENT_OS_DIR = os.path.dirname(TESTS_DIR)
WORKTREE_SCRIPT = os.path.join(MULTI_AGENT_OS_DIR, "tools", "worktree_review.sh")


class TestWorktreeReview(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="agent-os-wt-test-")
        subprocess.run(["git", "init", "-b", "main"], cwd=self.test_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test Agent"], cwd=self.test_dir, check=True)
        subprocess.run(["git", "config", "user.email", "agent@example.com"], cwd=self.test_dir, check=True)

        # Base commit
        readme = os.path.join(self.test_dir, "README.md")
        with open(readme, "w") as f:
            f.write("# Hello\n")
        subprocess.run(["git", "add", "README.md"], cwd=self.test_dir, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.test_dir, check=True)

        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.test_dir, capture_output=True, text=True, check=True)
        self.head_sha = res.stdout.strip()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_help_flag_returns_zero(self):
        proc = subprocess.run([WORKTREE_SCRIPT, "--help"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Multi-Agent OS v1.0.1 Git Worktree Review Tool", proc.stdout)

    def test_unsafe_path_rejected(self):
        # Attempting clean on /tmp/other-path outside whitelist
        proc = subprocess.run([WORKTREE_SCRIPT, "clean", "/tmp/unauthorized-dir"], cwd=self.test_dir, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Security Violation", proc.stdout)

    def test_non_worktree_directory_rejected(self):
        # Path inside whitelist prefix, but not a registered worktree
        fake_dir = f"/tmp/agent-review-fake-dir-{os.getpid()}"
        os.makedirs(fake_dir, exist_ok=True)
        try:
            with open(os.path.join(fake_dir, "file.txt"), "w") as f:
                f.write("important data\n")
            proc = subprocess.run([WORKTREE_SCRIPT, "clean", fake_dir], cwd=self.test_dir, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("Refusing to remove", proc.stdout)
            self.assertTrue(os.path.exists(fake_dir))
        finally:
            shutil.rmtree(fake_dir, ignore_errors=True)

    def test_non_worktree_with_git_file_not_deleted(self):
        # P0 regression test: directory contains a .git file but is NOT in git worktree list
        fake_dir = f"/tmp/agent-review-git-fake-{os.getpid()}"
        os.makedirs(fake_dir, exist_ok=True)
        try:
            with open(os.path.join(fake_dir, ".git"), "w") as f:
                f.write("gitdir: /fake/path\n")
            with open(os.path.join(fake_dir, "important.txt"), "w") as f:
                f.write("DO NOT DELETE THIS DATA\n")

            proc = subprocess.run([WORKTREE_SCRIPT, "clean", fake_dir], cwd=self.test_dir, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("Refusing to remove", proc.stdout)
            # Ensure the directory and files were NOT deleted
            self.assertTrue(os.path.exists(fake_dir))
            self.assertTrue(os.path.exists(os.path.join(fake_dir, "important.txt")))
        finally:
            shutil.rmtree(fake_dir, ignore_errors=True)

    def test_valid_lifecycle_start_and_clean(self):
        target_wt = f"/tmp/agent-review-test-{os.getpid()}"
        try:
            # 1. Start worktree
            proc_start = subprocess.run([WORKTREE_SCRIPT, "start", self.head_sha, target_wt], cwd=self.test_dir, capture_output=True, text=True)
            self.assertEqual(proc_start.returncode, 0, f"STDOUT: {proc_start.stdout}\nSTDERR: {proc_start.stderr}")
            self.assertTrue(os.path.isdir(target_wt))
            self.assertTrue(os.path.exists(os.path.join(target_wt, "README.md")))

            # 2. Clean worktree
            proc_clean = subprocess.run([WORKTREE_SCRIPT, "clean", target_wt], cwd=self.test_dir, capture_output=True, text=True)
            self.assertEqual(proc_clean.returncode, 0, f"STDOUT: {proc_clean.stdout}\nSTDERR: {proc_clean.stderr}")
            self.assertFalse(os.path.exists(target_wt))
        finally:
            if os.path.exists(target_wt):
                subprocess.run(["git", "worktree", "remove", "--force", target_wt], cwd=self.test_dir, capture_output=True)
                shutil.rmtree(target_wt, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
