#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path

def run_tests():
    base_dir = Path(__file__).parent
    cases_file = base_dir / "cases.jsonl"
    lint_py = base_dir.parent / "lint" / "ncs-lint.py"

    with open(cases_file, "r", encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]

    print(f"Running {len(cases)} contract tests on {lint_py.name}...")
    passed = 0
    failed = 0

    for c in cases:
        cid = c["id"]
        text = c["text"]
        expected_warn = c["expected_warn"]
        expected_count = c.get("expected_count")
        expected_rules = c.get("expected_rules")

        proc = subprocess.run([sys.executable, str(lint_py), "--json", "-"], input=text, capture_output=True, text=True)
        try:
            res = json.loads(proc.stdout)
            warnings = res.get("warnings", [])
        except Exception as e:
            print(f"FAIL [{cid}]: Failed to parse JSON: {e}\nStdout: {proc.stdout}")
            failed += 1
            continue

        has_warn = len(warnings) > 0
        actual_rules = [w["rule"] for w in warnings]

        # 1. Check warning boolean
        if has_warn != expected_warn:
            failed += 1
            print(f"FAIL [{cid}]: expected_warn={expected_warn}, got {has_warn}")
            print(f"  Input: {text!r}")
            print(f"  Actual warnings: {warnings}")
            continue

        # 2. Check exact warning count if specified
        if expected_count is not None and len(warnings) != expected_count:
            failed += 1
            print(f"FAIL [{cid}]: expected_count={expected_count}, got {len(warnings)}")
            print(f"  Input: {text!r}")
            print(f"  Actual rules: {actual_rules}")
            continue

        # 3. Check rule contract if specified
        if expected_rules is not None:
            if sorted(actual_rules) != sorted(expected_rules):
                failed += 1
                print(f"FAIL [{cid}]: expected_rules={expected_rules}, got {actual_rules}")
                print(f"  Input: {text!r}")
                continue

        passed += 1

    # CLI Exit Code Contract Tests:
    # Exit Code 0: Clean file
    proc_clean = subprocess.run([sys.executable, str(lint_py), "-"], input="当前合理二手价约 $50-70。", capture_output=True, text=True)
    if proc_clean.returncode == 0:
        passed += 1
    else:
        failed += 1
        print(f"FAIL [exit_code_0]: expected 0, got {proc_clean.returncode}")

    # Exit Code 1: Warnings found
    proc_warn = subprocess.run([sys.executable, str(lint_py), "-"], input="这边建议直接发布。", capture_output=True, text=True)
    if proc_warn.returncode == 1:
        passed += 1
    else:
        failed += 1
        print(f"FAIL [exit_code_1]: expected 1, got {proc_warn.returncode}")

    # Exit Code 2: Nonexistent file
    proc_err = subprocess.run([sys.executable, str(lint_py), "nonexistent_file_xyz.txt"], capture_output=True, text=True)
    if proc_err.returncode == 2:
        passed += 1
    else:
        failed += 1
        print(f"FAIL [exit_code_2_nonexistent]: expected 2, got {proc_err.returncode}")

    # Exit Code 2: Both warning and missing file
    proc_both = subprocess.run([sys.executable, str(lint_py), "nonexistent_file_xyz.txt", str(cases_file)], capture_output=True, text=True)
    if proc_both.returncode == 2:
        passed += 1
    else:
        failed += 1
        print(f"FAIL [exit_code_2_both]: expected 2, got {proc_both.returncode}")

    # Preset flag tests
    proc_list = subprocess.run([sys.executable, str(lint_py), "--list-presets"], capture_output=True, text=True)
    if proc_list.returncode == 0 and "coding" in proc_list.stdout and "investment" in proc_list.stdout:
        passed += 1
    else:
        failed += 1
        print(f"FAIL [preset_list]: expected 0 with preset names, got {proc_list.returncode}")

    proc_preset_warn = subprocess.run([sys.executable, str(lint_py), "--preset", "investment", "-"], input="这个投资项目稳赚不赔。", capture_output=True, text=True)
    if proc_preset_warn.returncode == 1 and "guaranteed_return" in proc_preset_warn.stdout:
        passed += 1
    else:
        failed += 1
        print(f"FAIL [preset_investment_warn]: expected 1, got {proc_preset_warn.returncode}")

    print(f"\nContract Test Results: {passed} passed, {failed} failed.")
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(run_tests())
