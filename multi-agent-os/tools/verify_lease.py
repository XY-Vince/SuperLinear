#!/usr/bin/env python3
"""
verify_lease.py — Multi-Agent OS v1.0.1 Merge Gate Verifier (SuperLinear Hardened Edition)

Validates task lease metadata against the current git diff:
1. Strict schema compliance (all required fields present and valid).
2. Status is ACTIVE.
3. Expiration date is valid ISO 8601 with timezone and has not expired.
4. granted_at is valid ISO 8601 with timezone and <= now < expires_at.
5. granted_by is strictly 'human' (Human Control Authority).
6. branch strictly matches active branch or target_sha belongs to declared branch ref.
7. base_sha and target_sha are valid git commit objects.
8. base_sha is an ancestor of target_sha.
9. All changed files are subsets of touched_areas.
10. Risk monotonicity: declared_risk >= computed_minimum_risk based on touched paths and diff size.
11. Control-plane assets (.agents, .codex, multi-agent-os) are protected and enforce R3.
12. Authoritative lease loading from trusted git ref (prevents branch self-authorization).
"""

import sys
import os
import re
import fnmatch
import argparse
import subprocess
from datetime import datetime, timezone

PROTECTED_PATHS = [
    "auth/**", "security/**", "migrations/**", "schema/**",
    "package.json", "package-lock.json", "poetry.lock", "Cargo.lock", "requirements.txt",
    ".github/**", "control/**", "AGENT_PROTOCOL.md", "PROJECT.md", "AGENTS.md",
    "Dockerfile", "docker-compose.yml",
    ".agents/**", ".codex/**",
    "multi-agent-os/core/**", "multi-agent-os/adapters/**", "multi-agent-os/tools/**",
    "multi-agent-os/SPEC_V1_FINAL.md", "multi-agent-os/MIGRATION_SETUP_GUIDE.md"
]

NON_RUNTIME_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".rst", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".drawio"
}

ALLOWED_WRITERS = {"ag", "antigravity", "codex", "muse", "workbuddy"}
ALLOWED_GRANTORS = {"human"}

RISK_LEVELS = ["R0", "R1", "R2", "R3"]
RISK_ORDER = {lvl: idx for idx, lvl in enumerate(RISK_LEVELS)}

REQUIRED_FIELDS = [
    "task", "status", "writer", "granted_by", "branch",
    "base_sha", "touched_areas", "declared_risk", "granted_at", "expires_at"
]


def run_git_args(args: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    """Execute git command safely without shell=True."""
    cmd = ["git"] + args
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return res.returncode, res.stdout.strip(), res.stderr.strip()


def parse_simple_yaml_text(text: str) -> dict:
    data = {}
    current_key = None
    list_items = []

    for line in text.splitlines():
        clean = line.strip()
        if not clean or clean.startswith('#'):
            continue

        if ':' in clean and not clean.startswith('-'):
            if current_key and list_items:
                data[current_key] = list_items
                list_items = []
            key, val = clean.split(':', 1)
            key = key.strip()
            val = val.split('#')[0].strip().strip('"').strip("'")
            if val:
                data[key] = val
                current_key = None
            else:
                current_key = key
        elif clean.startswith('- ') and current_key:
            val = clean[2:].split('#')[0].strip().strip('"').strip("'")
            list_items.append(val)

    if current_key and list_items:
        data[current_key] = list_items

    return data


def matches_any(path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(path, pat):
            return True
        if pat.endswith("/**") and (path.startswith(pat[:-3] + "/") or path == pat[:-3]):
            return True
    return False


def is_non_runtime_file(filepath: str) -> bool:
    # Any control plane or protected path file is NEVER a benign non-runtime file
    if matches_any(filepath, PROTECTED_PATHS):
        return False
    _, ext = os.path.splitext(filepath)
    return ext.lower() in NON_RUNTIME_EXTENSIONS


def validate_commit_ref(ref: str, param_name: str) -> str:
    """Validate that ref points to an actual commit object using rev-parse."""
    if not ref or ref.startswith("-"):
        raise ValueError(f"Invalid {param_name}: '{ref}' cannot be empty or start with '-'")

    code, stdout, stderr = run_git_args(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
    if code != 0 or not stdout:
        raise ValueError(f"Invalid {param_name}: '{ref}' is not a valid commit object ({stderr or 'not found'})")
    return stdout


def load_lease_data(task_file: str, lease_ref: str | None, allow_uncommitted: bool) -> tuple[dict, str]:
    """
    Load lease data from trusted git ref or local file.
    Fail-closed: branch-local files are rejected unless allow_uncommitted=True.
    Returns (meta_dict, source_description).
    """
    code_top, top_level, _ = run_git_args(["rev-parse", "--show-toplevel"])
    if code_top == 0 and top_level:
        git_path = os.path.relpath(os.path.realpath(task_file), os.path.realpath(top_level)).replace("\\", "/")
    else:
        git_path = os.path.relpath(task_file).replace("\\", "/")

    if lease_ref:
        code, stdout, stderr = run_git_args(["show", f"{lease_ref}:{git_path}"])
        if code != 0:
            raise ValueError(f"Failed to read task lease '{git_path}' from trusted ref '{lease_ref}': {stderr}")
        return parse_simple_yaml_text(stdout), f"trusted git ref '{lease_ref}:{git_path}'"

    if allow_uncommitted:
        if not os.path.isfile(task_file):
            raise ValueError(f"Task file not found: {task_file}")
        with open(task_file, "r", encoding="utf-8") as f:
            content = f.read()
        return parse_simple_yaml_text(content), f"local file '{task_file}' (--allow-uncommitted-lease [TEST-ONLY])"

    # Default heuristic: check if origin/main or main has this file
    for candidate_ref in ["origin/main", "main"]:
        code, stdout, _ = run_git_args(["rev-parse", "--verify", "--quiet", candidate_ref])
        if code == 0:
            code2, stdout2, _ = run_git_args(["show", f"{candidate_ref}:{git_path}"])
            if code2 == 0:
                return parse_simple_yaml_text(stdout2), f"authoritative ref '{candidate_ref}:{git_path}'"

    # Fail closed: do NOT fall back to uncommitted branch copy
    raise ValueError(
        f"Authoritative lease '{git_path}' not found in trusted git refs (checked 'origin/main', 'main').\n"
        "Branch-local copies cannot self-authorize merges into canonical main (Fail-Closed).\n"
        "To test locally prior to committing to main, pass --allow-uncommitted-lease."
    )


def verify_lease(task_file: str, target_sha: str = "HEAD", lease_ref: str | None = None, allow_uncommitted: bool = False) -> None:
    print(f"=== Multi-Agent OS v1.0.1 Lease & Merge Gate Verifier ===")

    # 1. Load Lease
    try:
        meta, lease_source = load_lease_data(task_file, lease_ref, allow_uncommitted)
    except ValueError as ve:
        print(f"[FAIL] {ve}")
        sys.exit(1)

    print(f"Loaded lease from: {lease_source}")
    if allow_uncommitted:
        print("[WARN] Running in TEST-ONLY mode (--allow-uncommitted-lease). This check is NOT valid for canonical merge gating.")

    # 2. Strict Schema Validation
    missing_fields = [f for f in REQUIRED_FIELDS if f not in meta or meta[f] is None or meta[f] == ""]
    if missing_fields:
        print(f"[FAIL] Lease schema error: missing required field(s): {', '.join(missing_fields)}")
        sys.exit(1)

    # 3. Validate Metadata (Writer, Grantor, Branch)
    writer = str(meta.get("writer", "")).strip().lower()
    if writer not in ALLOWED_WRITERS:
        print(f"[FAIL] Invalid writer: '{writer}'. Allowed: {', '.join(sorted(ALLOWED_WRITERS))}")
        sys.exit(1)

    granted_by = str(meta.get("granted_by", "")).strip().lower()
    if granted_by not in ALLOWED_GRANTORS:
        print(f"[FAIL] Invalid granted_by: '{granted_by}'. Only 'human' possesses lease authorization authority per AGENT_PROTOCOL.md.")
        sys.exit(1)

    branch = str(meta.get("branch", "")).strip()
    if not branch:
        print(f"[FAIL] Lease 'branch' cannot be empty")
        sys.exit(1)

    print(f"Task: {meta.get('task')} | Writer: {writer} | Granted By: {granted_by} | Branch: {branch}")

    # 4. Status check
    status = str(meta.get("status", "")).strip().upper()
    if status != "ACTIVE":
        print(f"[FAIL] Lease status is not ACTIVE (current: '{status}')")
        sys.exit(1)
    print("✓ Lease status is ACTIVE")

    # 5. Strict Timestamp Parsing (granted_at & expires_at)
    now_dt = datetime.now(timezone.utc)
    granted_at_str = str(meta.get("granted_at", "")).strip()
    expires_at_str = str(meta.get("expires_at", "")).strip()

    try:
        granted_dt = datetime.fromisoformat(granted_at_str)
        if granted_dt.tzinfo is None:
            print(f"[FAIL] Lease granted_at date '{granted_at_str}' must include timezone (ISO 8601)")
            sys.exit(1)
        if granted_dt > now_dt:
            print(f"[FAIL] Lease is not yet active: granted_at ({granted_at_str}) is in the future (current UTC: {now_dt.isoformat()})")
            sys.exit(1)
    except Exception as e:
        print(f"[FAIL] Lease granted_at date parsing failed for '{granted_at_str}': {e}")
        sys.exit(1)

    try:
        exp_dt = datetime.fromisoformat(expires_at_str)
        if exp_dt.tzinfo is None:
            print(f"[FAIL] Lease expiration date '{expires_at_str}' must include timezone (ISO 8601)")
            sys.exit(1)
        if now_dt >= exp_dt:
            print(f"[FAIL] Lease expired at {expires_at_str} (current UTC: {now_dt.isoformat()})")
            sys.exit(1)
        if granted_dt >= exp_dt:
            print(f"[FAIL] Lease granted_at ({granted_at_str}) cannot be equal to or later than expires_at ({expires_at_str})")
            sys.exit(1)
        print(f"✓ Lease is valid until {expires_at_str} (active since {granted_at_str})")
    except Exception as e:
        print(f"[FAIL] Lease expiration date parsing failed for '{expires_at_str}': {e}")
        sys.exit(1)

    # 6. Risk parsing
    declared_risk = str(meta.get("declared_risk", "")).strip().upper()
    if declared_risk not in RISK_ORDER:
        print(f"[FAIL] Declared risk '{declared_risk}' is invalid. Allowed: {', '.join(RISK_LEVELS)}")
        sys.exit(1)
    print(f"✓ Declared risk level: {declared_risk}")

    # 7. Validate Commit SHAs
    base_sha_raw = str(meta.get("base_sha", "")).strip()
    if base_sha_raw.startswith("000000"):
        print(f"[FAIL] base_sha cannot be a placeholder ('{base_sha_raw}')")
        sys.exit(1)

    try:
        base_sha_resolved = validate_commit_ref(base_sha_raw, "base_sha")
        target_sha_resolved = validate_commit_ref(target_sha, "target_sha")
    except ValueError as ve:
        print(f"[FAIL] {ve}")
        sys.exit(1)

    # 8. Strict Branch Binding Validation (Fail-Closed)
    code_br, cur_branch, _ = run_git_args(["rev-parse", "--abbrev-ref", "HEAD"])
    if code_br == 0 and cur_branch and cur_branch != "HEAD":
        if cur_branch != branch:
            print(f"[FAIL] Branch mismatch: active git branch '{cur_branch}' does not match lease branch '{branch}'")
            sys.exit(1)
    else:
        # On detached HEAD: target_sha must belong to declared branch
        branch_ref_found = False
        for ref_candidate in [f"refs/heads/{branch}", f"refs/remotes/origin/{branch}", branch]:
            code_ref, sha_candidate, _ = run_git_args(["rev-parse", "--verify", "--quiet", ref_candidate])
            if code_ref == 0 and sha_candidate:
                branch_ref_found = True
                code_anc, _, _ = run_git_args(["merge-base", "--is-ancestor", target_sha_resolved, sha_candidate])
                if code_anc != 0:
                    print(f"[FAIL] Branch mismatch: target SHA ({target_sha_resolved[:8]}) does not belong to lease branch '{branch}' ({sha_candidate[:8]})")
                    sys.exit(1)
                break
        if not branch_ref_found:
            print(f"[FAIL] Branch mismatch: declared branch '{branch}' does not exist as a local or remote ref")
            sys.exit(1)
    print(f"✓ Branch binding verified for '{branch}'")

    # 9. Ancestry check
    code, _, _ = run_git_args(["merge-base", "--is-ancestor", base_sha_resolved, target_sha_resolved])
    if code != 0:
        print(f"[FAIL] base_sha ({base_sha_resolved[:8]}) is NOT an ancestor of target_sha ({target_sha_resolved[:8]})")
        sys.exit(1)
    print(f"✓ base_sha ({base_sha_resolved[:8]}) is verified ancestor of target_sha ({target_sha_resolved[:8]})")

    # 10. Changed files subset of touched_areas
    touched_areas = meta.get("touched_areas", [])
    if isinstance(touched_areas, str):
        touched_areas = [touched_areas]

    code, diff_out, err = run_git_args(["diff", "--name-only", f"{base_sha_resolved}...{target_sha_resolved}"])
    if code != 0:
        print(f"[FAIL] git diff failed: {err}")
        sys.exit(1)

    changed_files = [f.strip() for f in diff_out.splitlines() if f.strip()]
    print(f"Checking {len(changed_files)} changed file(s) against touched_areas...")

    violations = [f for f in changed_files if not matches_any(f, touched_areas)]
    if violations:
        print(f"[FAIL] Changed files outside touched_areas ({len(violations)} file(s)):")
        for v in violations:
            print(f"  - {v}")
        sys.exit(1)
    print("✓ All changed files fall within declared touched_areas")

    # 11. Risk Monotonicity & Path Floor Calculation
    # Check if protected paths were touched
    protected_violations = [f for f in changed_files if matches_any(f, PROTECTED_PATHS)]

    # Compute shortstat
    code, stat_out, _ = run_git_args(["diff", "--shortstat", f"{base_sha_resolved}...{target_sha_resolved}"])
    insertions, deletions = 0, 0
    m_ins = re.search(r'(\d+)\s+insertion', stat_out)
    m_del = re.search(r'(\d+)\s+deletion', stat_out)
    if m_ins: insertions = int(m_ins.group(1))
    if m_del: deletions = int(m_del.group(1))
    total_lines = insertions + deletions

    # Auto-calculate minimum risk
    if protected_violations:
        computed_min_risk = "R3"
    elif len(changed_files) > 5 or total_lines > 100:
        computed_min_risk = "R2"
    elif len(changed_files) > 0:
        # Check if purely non-runtime documentation / metadata changes
        all_non_runtime = all(is_non_runtime_file(f) for f in changed_files)
        if all_non_runtime:
            computed_min_risk = "R0"
        else:
            computed_min_risk = "R1"
    else:
        computed_min_risk = "R0"

    print(f"Diff stats: {len(changed_files)} file(s), {total_lines} line(s) changed (+{insertions}/-{deletions})")
    print(f"Computed minimum risk based on diff: {computed_min_risk}")

    if RISK_ORDER[declared_risk] < RISK_ORDER[computed_min_risk]:
        print(f"[FAIL] Risk Monotonicity Violation: declared risk '{declared_risk}' is lower than required minimum '{computed_min_risk}'")
        if protected_violations:
            print(f"  Touched protected path(s) requiring R3: {protected_violations}")
        if len(changed_files) > 5 or total_lines > 100:
            print(f"  Diff size ({len(changed_files)} files, {total_lines} lines) exceeds R1 bounds (5 files, 100 lines)")
        sys.exit(1)
    print(f"✓ Risk monotonicity satisfied ({declared_risk} >= {computed_min_risk})")

    # 12. Summary
    print("\n[PASS] All Lease & Physical Diff Gate criteria verified successfully.")
    print("Scope verified: Lease schema, git ancestry, touched_areas subset, diff size, risk monotonicity.")
    print("External gates note: CI execution and peer review records are verified via GitHub Ruleset / Review logs.")


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Agent OS v1.0.1 Lease & Merge Gate Verifier (SuperLinear Hardened Edition)"
    )
    parser.add_argument("task_file", help="Path to task lease YAML file (e.g. control/tasks/T-001.yaml)")
    parser.add_argument("target_sha", nargs="?", default="HEAD", help="Target branch, commit, or revision (default: HEAD)")
    parser.add_argument("--lease-ref", default=None, help="Authoritative git ref to read lease from (e.g. origin/main, main)")
    parser.add_argument("--allow-uncommitted-lease", action="store_true", help="Allow reading uncommitted local lease file directly (TEST-ONLY)")

    args = parser.parse_args()
    verify_lease(args.task_file, args.target_sha, args.lease_ref, args.allow_uncommitted_lease)


if __name__ == "__main__":
    main()
