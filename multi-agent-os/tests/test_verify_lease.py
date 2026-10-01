#!/usr/bin/env python3
"""
test_verify_lease.py — Unit test suite for verify_lease.py (Multi-Agent OS v1.0.1)
"""

import os
import sys
import shutil
import tempfile
import unittest
import subprocess
from datetime import datetime, timezone, timedelta

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
MULTI_AGENT_OS_DIR = os.path.dirname(TESTS_DIR)
VERIFY_LEASE_SCRIPT = os.path.join(MULTI_AGENT_OS_DIR, "tools", "verify_lease.py")


class TestVerifyLease(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="agent-os-lease-test-")
        # Initialize a temporary git repository for tests
        subprocess.run(["git", "init", "-b", "main"], cwd=self.test_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test Agent"], cwd=self.test_dir, check=True)
        subprocess.run(["git", "config", "user.email", "agent@example.com"], cwd=self.test_dir, check=True)

        # Create base commit on main
        self.initial_file = os.path.join(self.test_dir, "README.md")
        with open(self.initial_file, "w") as f:
            f.write("# Sample Repo\n")
        subprocess.run(["git", "add", "README.md"], cwd=self.test_dir, check=True)
        subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=self.test_dir, check=True)

        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.test_dir, capture_output=True, text=True, check=True)
        self.base_sha = res.stdout.strip()

        # Create feature branch
        subprocess.run(["git", "checkout", "-b", "feat/test"], cwd=self.test_dir, check=True)

        # Create feature commit
        self.feature_file = os.path.join(self.test_dir, "src", "feature.py")
        os.makedirs(os.path.dirname(self.feature_file), exist_ok=True)
        with open(self.feature_file, "w") as f:
            f.write("def run():\n    return 42\n")
        subprocess.run(["git", "add", "."], cwd=self.test_dir, check=True)
        subprocess.run(["git", "commit", "-m", "feat: add feature"], cwd=self.test_dir, check=True)

        res2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.test_dir, capture_output=True, text=True, check=True)
        self.target_sha = res2.stdout.strip()

        self.valid_granted = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.future_granted = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        self.valid_expiry = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        self.past_expiry = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def write_task_file(self, content_dict: dict, rel_path: str = "task.yaml") -> str:
        task_path = os.path.join(self.test_dir, rel_path)
        os.makedirs(os.path.dirname(task_path), exist_ok=True)
        lines = []
        for k, v in content_dict.items():
            if isinstance(v, list):
                lines.append(f"{k}:")
                for item in v:
                    lines.append(f"  - \"{item}\"")
            else:
                lines.append(f"{k}: \"{v}\"")
        with open(task_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return task_path

    def run_verify(self, task_file: str, target: str = "HEAD", extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
        cmd = [sys.executable, VERIFY_LEASE_SCRIPT, task_file, target]
        if extra_args:
            cmd.extend(extra_args)
        return subprocess.run(cmd, cwd=self.test_dir, capture_output=True, text=True)

    def test_valid_lease_passes_with_allow_uncommitted(self):
        task_data = {
            "task": "T-001",
            "status": "ACTIVE",
            "writer": "antigravity",
            "granted_by": "human",
            "branch": "feat/test",
            "base_sha": self.base_sha,
            "touched_areas": ["src/**"],
            "declared_risk": "R1",
            "granted_at": self.valid_granted,
            "expires_at": self.valid_expiry
        }
        task_file = self.write_task_file(task_data)
        proc = self.run_verify(task_file, self.target_sha, ["--allow-uncommitted-lease"])
        self.assertEqual(proc.returncode, 0, f"STDOUT: {proc.stdout}\nSTDERR: {proc.stderr}")
        self.assertIn("[PASS]", proc.stdout)

    def test_uncommitted_lease_rejected_by_default(self):
        # P0 regression test: uncommitted task file without --allow-uncommitted-lease MUST fail closed
        task_data = {
            "task": "T-001",
            "status": "ACTIVE",
            "writer": "antigravity",
            "granted_by": "human",
            "branch": "feat/test",
            "base_sha": self.base_sha,
            "touched_areas": ["src/**"],
            "declared_risk": "R1",
            "granted_at": self.valid_granted,
            "expires_at": self.valid_expiry
        }
        task_file = self.write_task_file(task_data, "control/tasks/T-001.yaml")
        # Do not pass --allow-uncommitted-lease
        proc = self.run_verify(task_file, self.target_sha)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Authoritative lease 'control/tasks/T-001.yaml' not found in trusted git refs", proc.stdout)
        self.assertIn("Fail-Closed", proc.stdout)

    def test_committed_lease_on_main_passes(self):
        # Commit lease to main first
        subprocess.run(["git", "checkout", "main"], cwd=self.test_dir, check=True)
        task_data = {
            "task": "T-001",
            "status": "ACTIVE",
            "writer": "antigravity",
            "granted_by": "human",
            "branch": "feat/test",
            "base_sha": self.base_sha,
            "touched_areas": ["src/**"],
            "declared_risk": "R1",
            "granted_at": self.valid_granted,
            "expires_at": self.valid_expiry
        }
        task_file = self.write_task_file(task_data, "control/tasks/T-001.yaml")
        subprocess.run(["git", "add", "control/tasks/T-001.yaml"], cwd=self.test_dir, check=True)
        subprocess.run(["git", "commit", "-m", "chore: authorize lease T-001"], cwd=self.test_dir, check=True)

        # Return to feature branch and verify
        subprocess.run(["git", "checkout", "feat/test"], cwd=self.test_dir, check=True)
        proc = self.run_verify(task_file, self.target_sha)
        self.assertEqual(proc.returncode, 0, f"STDOUT: {proc.stdout}\nSTDERR: {proc.stderr}")
        self.assertIn("authoritative ref 'main:control/tasks/T-001.yaml'", proc.stdout)
        self.assertIn("[PASS]", proc.stdout)

    def test_codex_cannot_grant_lease(self):
        # Sol P1 audit: Only human may grant leases per AGENT_PROTOCOL.md
        task_data = {
            "task": "T-001",
            "status": "ACTIVE",
            "writer": "antigravity",
            "granted_by": "codex",  # Unauthorized grantor
            "branch": "feat/test",
            "base_sha": self.base_sha,
            "touched_areas": ["src/**"],
            "declared_risk": "R1",
            "granted_at": self.valid_granted,
            "expires_at": self.valid_expiry
        }
        task_file = self.write_task_file(task_data)
        proc = self.run_verify(task_file, self.target_sha, ["--allow-uncommitted-lease"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Only 'human' possesses lease authorization authority", proc.stdout)

    def test_branch_mismatch_fails_closed(self):
        # Sol P1 audit: Active branch mismatch must fail closed
        task_data = {
            "task": "T-001",
            "status": "ACTIVE",
            "writer": "antigravity",
            "granted_by": "human",
            "branch": "feat/other-branch",  # Mismatch with feat/test
            "base_sha": self.base_sha,
            "touched_areas": ["src/**"],
            "declared_risk": "R1",
            "granted_at": self.valid_granted,
            "expires_at": self.valid_expiry
        }
        task_file = self.write_task_file(task_data)
        proc = self.run_verify(task_file, self.target_sha, ["--allow-uncommitted-lease"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Branch mismatch: active git branch 'feat/test' does not match lease branch 'feat/other-branch'", proc.stdout)

    def test_future_granted_at_fails_closed(self):
        # Sol audit: granted_at in the future must fail closed
        task_data = {
            "task": "T-001",
            "status": "ACTIVE",
            "writer": "antigravity",
            "granted_by": "human",
            "branch": "feat/test",
            "base_sha": self.base_sha,
            "touched_areas": ["src/**"],
            "declared_risk": "R1",
            "granted_at": self.future_granted,  # Future grant
            "expires_at": self.valid_expiry
        }
        task_file = self.write_task_file(task_data)
        proc = self.run_verify(task_file, self.target_sha, ["--allow-uncommitted-lease"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Lease is not yet active: granted_at", proc.stdout)

    def test_command_injection_rejected(self):
        task_data = {
            "task": "T-001",
            "status": "ACTIVE",
            "writer": "antigravity",
            "granted_by": "human",
            "branch": "feat/test",
            "base_sha": f"{self.base_sha}; touch /tmp/pwned",
            "touched_areas": ["src/**"],
            "declared_risk": "R1",
            "granted_at": self.valid_granted,
            "expires_at": self.valid_expiry
        }
        task_file = self.write_task_file(task_data)
        proc = self.run_verify(task_file, self.target_sha, ["--allow-uncommitted-lease"])
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(os.path.exists("/tmp/pwned"))
        self.assertIn("is not a valid commit object", proc.stdout + proc.stderr)

    def test_missing_fields_fail_closed(self):
        task_data = {
            "task": "T-001",
            # missing status
            "writer": "antigravity",
            "branch": "feat/test",
            "base_sha": self.base_sha,
            "touched_areas": ["src/**"],
            "declared_risk": "R1",
            "expires_at": self.valid_expiry
        }
        task_file = self.write_task_file(task_data)
        proc = self.run_verify(task_file, self.target_sha, ["--allow-uncommitted-lease"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("missing required field", proc.stdout)

    def test_invalid_writer_fails(self):
        task_data = {
            "task": "T-001",
            "status": "ACTIVE",
            "writer": "malicious_bot",
            "granted_by": "human",
            "branch": "feat/test",
            "base_sha": self.base_sha,
            "touched_areas": ["src/**"],
            "declared_risk": "R1",
            "granted_at": self.valid_granted,
            "expires_at": self.valid_expiry
        }
        task_file = self.write_task_file(task_data)
        proc = self.run_verify(task_file, self.target_sha, ["--allow-uncommitted-lease"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Invalid writer", proc.stdout)

    def test_naive_timezone_fails(self):
        task_data = {
            "task": "T-001",
            "status": "ACTIVE",
            "writer": "antigravity",
            "granted_by": "human",
            "branch": "feat/test",
            "base_sha": self.base_sha,
            "touched_areas": ["src/**"],
            "declared_risk": "R1",
            "granted_at": "2026-10-01T12:00:00",
            "expires_at": "2026-10-05T12:00:00"  # no timezone offset!
        }
        task_file = self.write_task_file(task_data)
        proc = self.run_verify(task_file, self.target_sha, ["--allow-uncommitted-lease"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("must include timezone", proc.stdout)

    def test_expired_lease_fails(self):
        task_data = {
            "task": "T-001",
            "status": "ACTIVE",
            "writer": "antigravity",
            "granted_by": "human",
            "branch": "feat/test",
            "base_sha": self.base_sha,
            "touched_areas": ["src/**"],
            "declared_risk": "R1",
            "granted_at": self.valid_granted,
            "expires_at": self.past_expiry
        }
        task_file = self.write_task_file(task_data)
        proc = self.run_verify(task_file, self.target_sha, ["--allow-uncommitted-lease"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Lease expired at", proc.stdout)

    def test_r0_pure_docs_allowed(self):
        # Commit purely modifying a markdown documentation file
        doc_file = os.path.join(self.test_dir, "docs", "guide.md")
        os.makedirs(os.path.dirname(doc_file), exist_ok=True)
        with open(doc_file, "w") as f:
            f.write("# Guide\nSome doc update.\n")
        subprocess.run(["git", "add", "docs/guide.md"], cwd=self.test_dir, check=True)
        subprocess.run(["git", "commit", "-m", "docs: update guide"], cwd=self.test_dir, check=True)
        target_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.test_dir, capture_output=True, text=True).stdout.strip()

        task_data = {
            "task": "T-DOC",
            "status": "ACTIVE",
            "writer": "antigravity",
            "granted_by": "human",
            "branch": "feat/test",
            "base_sha": self.target_sha,  # diff is just docs/guide.md
            "touched_areas": ["docs/**"],
            "declared_risk": "R0",
            "granted_at": self.valid_granted,
            "expires_at": self.valid_expiry
        }
        task_file = self.write_task_file(task_data)
        proc = self.run_verify(task_file, target_sha, ["--allow-uncommitted-lease"])
        self.assertEqual(proc.returncode, 0, f"STDOUT: {proc.stdout}\nSTDERR: {proc.stderr}")
        self.assertIn("Computed minimum risk based on diff: R0", proc.stdout)
        self.assertIn("[PASS]", proc.stdout)

    def test_r0_touching_protected_path_fails(self):
        # Create commit modifying AGENTS.md (protected path)
        agents_file = os.path.join(self.test_dir, "AGENTS.md")
        with open(agents_file, "w") as f:
            f.write("# New Agents\n")
        subprocess.run(["git", "add", "AGENTS.md"], cwd=self.test_dir, check=True)
        subprocess.run(["git", "commit", "-m", "modify agents"], cwd=self.test_dir, check=True)
        target_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.test_dir, capture_output=True, text=True).stdout.strip()

        task_data = {
            "task": "T-002",
            "status": "ACTIVE",
            "writer": "antigravity",
            "granted_by": "human",
            "branch": "feat/test",
            "base_sha": self.base_sha,
            "touched_areas": ["src/**", "AGENTS.md"],
            "declared_risk": "R0",  # Declaring R0 for protected path change!
            "granted_at": self.valid_granted,
            "expires_at": self.valid_expiry
        }
        task_file = self.write_task_file(task_data)
        proc = self.run_verify(task_file, target_sha, ["--allow-uncommitted-lease"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Risk Monotonicity Violation", proc.stdout)
        self.assertIn("Touched protected path(s) requiring R3", proc.stdout)

    def test_control_plane_skill_or_adapter_enforces_r3(self):
        # Sol P1 audit: .agents/** and multi-agent-os/adapters/** MUST enforce R3
        skill_file = os.path.join(self.test_dir, ".agents", "skills", "my-skill", "SKILL.md")
        os.makedirs(os.path.dirname(skill_file), exist_ok=True)
        with open(skill_file, "w") as f:
            f.write("# Custom Agent Skill\n")
        subprocess.run(["git", "add", "."], cwd=self.test_dir, check=True)
        subprocess.run(["git", "commit", "-m", "feat: add agent skill"], cwd=self.test_dir, check=True)
        target_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.test_dir, capture_output=True, text=True).stdout.strip()

        task_data = {
            "task": "T-SKILL",
            "status": "ACTIVE",
            "writer": "antigravity",
            "granted_by": "human",
            "branch": "feat/test",
            "base_sha": self.base_sha,
            "touched_areas": ["src/**", ".agents/**"],
            "declared_risk": "R0",  # Declaring R0 for skill markdown change!
            "granted_at": self.valid_granted,
            "expires_at": self.valid_expiry
        }
        task_file = self.write_task_file(task_data)
        proc = self.run_verify(task_file, target_sha, ["--allow-uncommitted-lease"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Risk Monotonicity Violation", proc.stdout)
        self.assertIn("Touched protected path(s) requiring R3", proc.stdout)

    def test_r1_limits_exceeded_fails(self):
        # Create commit modifying > 100 lines
        big_file = os.path.join(self.test_dir, "src", "big.py")
        with open(big_file, "w") as f:
            for i in range(120):
                f.write(f"val_{i} = {i}\n")
        subprocess.run(["git", "add", "."], cwd=self.test_dir, check=True)
        subprocess.run(["git", "commit", "-m", "big diff"], cwd=self.test_dir, check=True)
        target_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.test_dir, capture_output=True, text=True).stdout.strip()

        task_data = {
            "task": "T-003",
            "status": "ACTIVE",
            "writer": "antigravity",
            "granted_by": "human",
            "branch": "feat/test",
            "base_sha": self.base_sha,
            "touched_areas": ["src/**"],
            "declared_risk": "R1",  # R1 limited to 100 lines
            "granted_at": self.valid_granted,
            "expires_at": self.valid_expiry
        }
        task_file = self.write_task_file(task_data)
        proc = self.run_verify(task_file, target_sha, ["--allow-uncommitted-lease"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Risk Monotonicity Violation", proc.stdout)
        self.assertIn("exceeds R1 bounds", proc.stdout)

    def test_touched_areas_out_of_bounds_fails(self):
        task_data = {
            "task": "T-004",
            "status": "ACTIVE",
            "writer": "antigravity",
            "granted_by": "human",
            "branch": "feat/test",
            "base_sha": self.base_sha,
            "touched_areas": ["docs/**"],  # But feature.py is in src/**
            "declared_risk": "R1",
            "granted_at": self.valid_granted,
            "expires_at": self.valid_expiry
        }
        task_file = self.write_task_file(task_data)
        proc = self.run_verify(task_file, self.target_sha, ["--allow-uncommitted-lease"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Changed files outside touched_areas", proc.stdout)


if __name__ == "__main__":
    unittest.main()
