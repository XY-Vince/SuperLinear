#!/usr/bin/env python3
"""
record_maintenance.py
CLI tool to record a maintenance event:
1. Calls shared inventory validation: fails fast before any file write
2. Handles --demo by writing isolated demonstration log without modifying inventory
3. Robustly updates YAML schedule regardless of field ordering (brand before id, etc.)
4. Detects conflicts and resumes previous partial failures upon retry
5. Re-reads and verifies YAML updates before declaring success
"""

import sys
import os
import re
import math
import datetime
import argparse
from pathlib import Path

# Add script dir to path to import inventory_io
script_dir = Path(__file__).resolve().parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

import inventory_io as io

def sanitize_id(dev_id):
    if not dev_id or "/" in dev_id or "\\" in dev_id or ".." in dev_id:
        return False
    return True

def update_yaml_maintenance_schedule(yaml_path, device_id, task_name, serviced_date, next_due_date, is_newer=True):
    """
    Safely and atomically updates maintenance schedule in YAML.
    Verifies that target values actually changed before replacing the file.
    Uses module-level os.replace so test fault injection works reliably.
    """
    if not is_newer:
        return True

    yaml_path = Path(yaml_path)
    with open(yaml_path, "r", encoding="utf-8") as f:
        orig_lines = f.readlines()

    ok, new_lines = io.update_yaml_lines(orig_lines, device_id, task_name, serviced_date, next_due_date)
    if not ok:
        return False

    tmp_path = yaml_path.with_name(f"{yaml_path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

        verified = io.load_yaml(tmp_path)
        if not isinstance(verified, dict):
            if tmp_path.exists():
                tmp_path.unlink()
            return False

        devs = verified.get("devices", [])
        target = next((d for d in devs if isinstance(d, dict) and str(d.get("id", "")).strip() == device_id), None)
        if not target:
            if tmp_path.exists():
                tmp_path.unlink()
            return False

        task_obj = target.get("maintenance_schedule", {}).get(task_name)
        if not isinstance(task_obj, dict):
            if tmp_path.exists():
                tmp_path.unlink()
            return False

        if str(task_obj.get("last_serviced")) != str(serviced_date):
            if tmp_path.exists():
                tmp_path.unlink()
            return False

        if str(task_obj.get("next_due")) != str(next_due_date):
            if tmp_path.exists():
                tmp_path.unlink()
            return False

        os.replace(tmp_path, yaml_path)
        return True
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        return False

def main():
    parser = argparse.ArgumentParser(description="Record a device maintenance event.")
    parser.add_argument("--device", required=True, help="Target Device ID (e.g. DEV-KTCH-2023-001)")
    parser.add_argument("--task", required=True, help="Task name (e.g. wash)")
    parser.add_argument("--date", required=True, help="Service date YYYY-MM-DD")
    parser.add_argument("--operator", default="User", help="Operator name")
    parser.add_argument("--cost", default=None, help="Cost in USD (optional, defaults to null/unknown)")
    parser.add_argument("--notes", default="", help="Maintenance notes")
    parser.add_argument("--demo", action="store_true", help="Mark as demonstration record")
    parser.add_argument("--inventory-dir", help="Custom inventory directory")
    parser.add_argument("--logs-dir", help="Custom maintenance_logs directory")
    args = parser.parse_args()

    # 1. Path safety check
    if not sanitize_id(args.device):
        print(f"ERROR: Invalid device ID '{args.device}'. Cannot contain path separators or '..'", file=sys.stderr)
        return 1

    project_root = Path(__file__).resolve().parent.parent
    inv_dir = Path(args.inventory_dir).resolve() if args.inventory_dir else (project_root / "inventory")
    logs_dir = Path(args.logs_dir).resolve() if args.logs_dir else (project_root / "maintenance_logs")

    today = datetime.date.today()
    s_date = io.to_date(args.date)
    if not s_date:
        print(f"ERROR: Invalid service date: '{args.date}'. Expected YYYY-MM-DD", file=sys.stderr)
        return 1

    # 2. Reject future dates
    if s_date > today:
        print(f"ERROR: Service date '{s_date}' cannot be in the future (today is {today})", file=sys.stderr)
        return 1

    # 3. Validate cost: omitted -> null; explicit 0 -> 0.00
    cost_str = "null"
    if args.cost is not None:
        try:
            cost_f = float(args.cost)
            if math.isnan(cost_f) or math.isinf(cost_f) or cost_f < 0:
                print(f"ERROR: Invalid cost: '{args.cost}'. Must be a non-negative number.", file=sys.stderr)
                return 1
            cost_str = f"{cost_f:.2f}"
        except (ValueError, TypeError):
            print(f"ERROR: Invalid cost: '{args.cost}'. Must be a non-negative number.", file=sys.stderr)
            return 1

    # 4. SHARED VALIDATION ENTRYPOINT: Fail fast before any file write
    ok, errors, all_devices, num_files, num_sns = io.load_and_validate_inventory_dir(inv_dir, today=today)
    if not ok:
        print(f"ERROR: Inventory failed validation with {len(errors)} error(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    # 5. Locate device and task from validated inventory
    target_dev = next((d for d in all_devices if isinstance(d, dict) and d.get("id") == args.device), None)
    if not target_dev:
        print(f"ERROR: Device '{args.device}' not found in {inv_dir}", file=sys.stderr)
        return 1

    src_file_name = target_dev.get("_source_file")
    target_file = (inv_dir / src_file_name) if src_file_name else None
    if not target_file or not target_file.exists():
        print(f"ERROR: Source inventory file for '{args.device}' not found in {inv_dir}", file=sys.stderr)
        return 1

    m_sched = target_dev.get("maintenance_schedule", {})
    if not isinstance(m_sched, dict) or args.task not in m_sched:
        print(f"ERROR: Task '{args.task}' not defined in maintenance_schedule for {args.device}", file=sys.stderr)
        return 1

    task_info = m_sched[args.task]
    if not isinstance(task_info, dict):
        print(f"ERROR: Task '{args.task}' info is invalid", file=sys.stderr)
        return 1

    interval_raw = task_info.get("interval_days")
    try:
        interval_days = int(interval_raw)
    except (ValueError, TypeError):
        print(f"ERROR: Task '{args.task}' interval_days is invalid: {interval_raw}", file=sys.stderr)
        return 1

    next_due_date = s_date + datetime.timedelta(days=interval_days)
    date_str_compact = s_date.strftime("%Y%m%d")
    notes_str = str(args.notes).strip()

    logs_dir.mkdir(parents=True, exist_ok=True)

    # 6. DEMONSTRATION MODE: Isolated namespace, zero modifications to real inventory
    is_demo = args.demo or (str(target_dev.get("record_kind", "")).strip().lower() == "example")
    if is_demo:
        log_filename = f"demo_{args.device}_{date_str_compact}_{args.task}.md"
        log_path = logs_dir / log_filename
        try:
            log_path.resolve().relative_to(logs_dir.resolve())
        except ValueError:
            print(f"ERROR: Log path escapes logs directory: {log_path}", file=sys.stderr)
            return 1

        log_content = f"""# 维护与耗材更换记录单 (Maintenance Log - Demonstration)

---
log_id: "DEMO-{date_str_compact}-{args.device}-{args.task}"
device_id: "{args.device}"
brand: "{target_dev.get('brand', '')}"
model: "{target_dev.get('model', '')}"
task: "{args.task}"
date: "{s_date.isoformat()}"
operator: "{args.operator}"
cost_usd: {cost_str}
interval_days: {interval_days}
next_due: "{next_due_date.isoformat()}"
record_kind: example
origin: demo_run
status: demonstration_only
---

## 1. 维护操作摘要
- **执行项目**: {args.task}
- **操作人员**: {args.operator}
- **记录备注**: {notes_str if notes_str else '演示操作（不修改真实台账）'}

## 2. 检查指标 (待用户实测核实，不自动预填完成)
- [ ] 操作流程已按官方手册要求执行完毕
- [ ] 设备功能测试正常
- [ ] 下次维护周期已推算至 {next_due_date.isoformat()}
"""
        log_path.write_text(log_content, encoding="utf-8")
        print(f"CREATED (Demo): {log_path.name}")
        print("INFO: Demonstration mode: real inventory schedule was not modified.")
        return 0

    # 7. REAL MODE: Complete transaction and recovery
    log_filename = f"{args.device}_{date_str_compact}_{args.task}.md"
    log_path = logs_dir / log_filename
    try:
        log_path.resolve().relative_to(logs_dir.resolve())
    except ValueError:
        print(f"ERROR: Log path escapes logs directory: {log_path}", file=sys.stderr)
        return 1

    cur_ls_raw = task_info.get("last_serviced")
    cur_ls_date = io.to_date(cur_ls_raw) if cur_ls_raw else None
    cur_nd_raw = task_info.get("next_due")
    cur_nd_date = io.to_date(cur_nd_raw) if cur_nd_raw else None
    is_newer = (cur_ls_date is None or s_date >= cur_ls_date)

    if log_path.exists():
        existing_text = log_path.read_text(encoding="utf-8")
        cost_match = re.search(r"cost_usd:\s*([^\n]+)", existing_text)
        notes_match = re.search(r"- \*\*记录备注\*\*:\s*([^\n]+)", existing_text)
        op_match = re.search(r"operator:\s*\"?([^\n\"]+)\"?", existing_text)
        task_match = re.search(r"task:\s*\"?([^\n\"]+)\"?", existing_text)

        existing_cost = cost_match.group(1).strip() if cost_match else ""
        existing_notes = notes_match.group(1).strip() if notes_match else ""
        existing_op = op_match.group(1).strip() if op_match else ""
        existing_task = task_match.group(1).strip() if task_match else ""

        conflict = False
        existing_cost_clean = existing_cost.lower().strip()
        new_cost_clean = cost_str.lower().strip()

        cost_conflict = False
        if existing_cost_clean in ("null", "none", "~", "") and new_cost_clean in ("null", "none", "~", ""):
            cost_conflict = False
        elif existing_cost_clean in ("null", "none", "~", "") or new_cost_clean in ("null", "none", "~", ""):
            cost_conflict = True
        else:
            try:
                if float(existing_cost_clean) != float(new_cost_clean):
                    cost_conflict = True
            except ValueError:
                if existing_cost_clean != new_cost_clean:
                    cost_conflict = True

        if cost_conflict:
            conflict = True

        expected_notes = notes_str if notes_str else "日常按周期常规维护"
        if notes_match and existing_notes != expected_notes:
            conflict = True
        if op_match and existing_op != args.operator:
            conflict = True
        if task_match and existing_task != args.task:
            conflict = True

        if conflict:
            print(f"ERROR: Conflict detected in existing log '{log_filename}'. Event parameters differ.", file=sys.stderr)
            return 1

        schedule_aligned = True
        if is_newer:
            if cur_ls_date != s_date or cur_nd_date != next_due_date:
                schedule_aligned = False

        if schedule_aligned:
            print(f"INFO: Log '{log_filename}' already exists and matches. Schedule already aligned. Re-run is idempotent.")
            return 0

        # Existing log found but schedule not yet updated (e.g. retry after injected failure)
        print(f"INFO: Existing log '{log_filename}' found from previous incomplete run. Completing inventory schedule update...")
        ok = update_yaml_maintenance_schedule(target_file, args.device, args.task, s_date.isoformat(), next_due_date.isoformat(), is_newer=True)
        if not ok:
            print(f"ERROR: Failed to update maintenance schedule in {target_file.name}", file=sys.stderr)
            return 1
        print(f"UPDATED: {target_file.name} -> {args.device}.{args.task} next_due updated to {next_due_date.isoformat()}")
        return 0

    # Write real maintenance log record
    log_content = f"""# 维护与耗材更换记录单 (Maintenance Log)

---
log_id: "MAINT-{date_str_compact}-{args.device}-{args.task}"
device_id: "{args.device}"
brand: "{target_dev.get('brand', '')}"
model: "{target_dev.get('model', '')}"
task: "{args.task}"
date: "{s_date.isoformat()}"
operator: "{args.operator}"
cost_usd: {cost_str}
interval_days: {interval_days}
next_due: "{next_due_date.isoformat()}"
record_kind: real
---

## 1. 维护操作摘要
- **执行项目**: {args.task}
- **操作人员**: {args.operator}
- **记录备注**: {notes_str if notes_str else '日常按周期常规维护'}

## 2. 检查指标 (待用户实测核实，不自动预填完成)
- [ ] 操作流程已按官方手册要求执行完毕
- [ ] 设备功能测试正常
- [ ] 下次维护周期已推算至 {next_due_date.isoformat()}
"""
    log_path.write_text(log_content, encoding="utf-8")
    print(f"CREATED: {log_path.name}")

    if not is_newer:
        print(f"INFO: Service date {s_date} is earlier than existing last_serviced ({cur_ls_date}). Schedule not regressed.")
        return 0

    ok = update_yaml_maintenance_schedule(target_file, args.device, args.task, s_date.isoformat(), next_due_date.isoformat(), is_newer=True)
    if not ok:
        print(f"ERROR: Failed to update maintenance schedule in {target_file.name}", file=sys.stderr)
        return 1

    print(f"UPDATED: {target_file.name} -> {args.device}.{args.task} next_due updated to {next_due_date.isoformat()}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
