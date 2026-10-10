#!/usr/bin/env python3
"""
status_report.py
Generates an operational status report across all household electronics:
- Shared centralized strict validation: fails fast on invalid data/files
- Conserves counts and registered costs across files sharing the same category
- Strict isolation of example records: excluded from real warranty, maintenance alerts, and hygiene
- Calibrated terminology: unverified equipment registrations and recorded purchase costs
- Accurate warranty status partition (in-warranty, expiring subset, expired, unknown)
- Filters out disposed devices from maintenance tasks
- Masks sensitive serial numbers in reports
"""

import sys
import json
import datetime
import argparse
from pathlib import Path

# Add script dir to path to import inventory_io
script_dir = Path(__file__).resolve().parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

import inventory_io as io

to_date = io.to_date
load_yaml = io.load_yaml

def mask_sn(sn):
    if not sn:
        return "N/A"
    s = str(sn).strip()
    if len(s) <= 4:
        return "****"
    return s[:4] + "***" + (s[-3:] if len(s) > 7 else "")

def main():
    parser = argparse.ArgumentParser(description="Generate HEM operational status report.")
    parser.add_argument("--inventory-dir", help="Custom inventory directory path")
    parser.add_argument("--as-of", help="Reference date YYYY-MM-DD for calculations (defaults to today)")
    parser.add_argument("--json", action="store_true", help="Output report in JSON format")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    inv_dir = Path(args.inventory_dir).resolve() if args.inventory_dir else (project_root / "inventory")

    today = datetime.date.today()
    if args.as_of:
        parsed_date = to_date(args.as_of)
        if parsed_date is None:
            if args.json:
                print(json.dumps({"error": "invalid_date_format", "as_of": args.as_of}))
            else:
                print(f"ERROR: Invalid --as-of date format: {args.as_of}. Expected YYYY-MM-DD", file=sys.stderr)
            return 1
        today = parsed_date

    # Centralized strict load & validate
    ok, errors, all_devices, num_files, num_sns = io.load_and_validate_inventory_dir(inv_dir, today=today)
    if not ok:
        print(f"ERROR: Inventory failed validation with {len(errors)} error(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        if args.json:
            print(json.dumps({"error": "validation_failed", "errors": errors, "real_devices_count": 0, "total_devices": 0}))
        return 1

    category_summary = {}
    example_devices = []
    real_devices = []

    # Initialize categories
    for d in all_devices:
        cat = d.get("_category", "unknown")
        if cat not in category_summary:
            category_summary[cat] = {
                "count": 0,
                "registered_cost": 0.0,
                "known_price_count": 0,
                "unknown_price_count": 0,
                # Backwards compatible keys for existing tests
                "held_count": 0,
                "held_value": 0.0,
                "total_value": 0.0,
                "example_count": 0,
                "example_value": 0.0,
            }

    for d in all_devices:
        cat = d.get("_category", "unknown")
        rec_kind = str(d.get("record_kind", "real")).strip().lower()
        is_example = (rec_kind == "example")
        if is_example:
            example_devices.append(d)
        else:
            real_devices.append(d)

        raw_p = d.get("purchase_price_usd")
        p_val = None
        if raw_p is not None and not isinstance(raw_p, bool) and str(raw_p).strip() != "":
            try:
                f_val = float(raw_p)
                if f_val >= 0:
                    p_val = f_val
            except (ValueError, TypeError):
                p_val = None

        st = str(d.get("status", "active")).strip().lower()

        if is_example:
            category_summary[cat]["example_count"] += 1
            if p_val is not None:
                category_summary[cat]["example_value"] += p_val
        else:
            category_summary[cat]["count"] += 1
            if p_val is not None:
                category_summary[cat]["registered_cost"] += p_val
                category_summary[cat]["known_price_count"] += 1
                category_summary[cat]["total_value"] += p_val
                if st in io.ACTIVE_STATUSES:
                    category_summary[cat]["held_count"] += 1
                    category_summary[cat]["held_value"] += p_val
            else:
                category_summary[cat]["unknown_price_count"] += 1
                if st in io.ACTIVE_STATUSES:
                    category_summary[cat]["held_count"] += 1

    total_devices = len(all_devices)
    grand_registered_cost = sum(info["registered_cost"] for info in category_summary.values())
    grand_known_price = sum(info["known_price_count"] for info in category_summary.values())
    grand_unknown_price = sum(info["unknown_price_count"] for info in category_summary.values())
    grand_real_count = len(real_devices)

    # 1. Warranty Partitioning (REAL devices only; examples strictly excluded)
    in_warranty = []
    expiring_soon = []
    expired_warranty = []
    unknown_warranty = []

    for d in real_devices:
        w_val = d.get("warranty_expiry")
        w_date = to_date(w_val)
        item_desc = f"{d.get('brand', 'Unknown')} {d.get('model', 'Model')} ({d.get('id', 'ID')})"
        if w_date is None:
            unknown_warranty.append(item_desc)
        else:
            days_left = (w_date - today).days
            if days_left >= 0:
                in_warranty.append((item_desc, w_date, days_left))
                if days_left <= 60:
                    expiring_soon.append((item_desc, w_date, days_left))
            else:
                expired_warranty.append((item_desc, w_date, abs(days_left)))

    # 2. Maintenance Tasks (REAL active devices only; examples and disposed filtered)
    maintenance_alerts = []

    for d in real_devices:
        st = str(d.get("status", "active")).strip().lower()
        if st in io.DISPOSED_STATUSES:
            continue

        m_sched = d.get("maintenance_schedule")
        if not isinstance(m_sched, dict):
            continue

        item_desc = f"{d.get('brand', 'Unknown')} {d.get('model', 'Model')} ({d.get('id', 'ID')})"
        for task_name, task_info in m_sched.items():
            if not isinstance(task_info, dict):
                continue
            raw_due = task_info.get("next_due")
            if raw_due is None or str(raw_due).strip() == "":
                continue

            due_date = to_date(raw_due)
            if due_date is None:
                continue

            lead_days = task_info.get("alert_lead_days", 14)
            try:
                lead_days = int(lead_days)
            except (ValueError, TypeError):
                lead_days = 14

            days_to_due = (due_date - today).days
            if days_to_due <= lead_days:
                maintenance_alerts.append({
                    "device": item_desc,
                    "task": task_name,
                    "due_date": due_date.isoformat(),
                    "days_to_due": days_to_due,
                    "is_overdue": days_to_due < 0,
                    "alert_lead_days": lead_days
                })

    maintenance_alerts.sort(key=lambda a: (0 if a["is_overdue"] else 1, a["days_to_due"]))

    disposed_count = sum(1 for d in real_devices if str(d.get("status", "")).lower() in io.DISPOSED_STATUSES)

    unverified_real_devices = [
        d for d in real_devices
        if str(d.get("verification_status", "unverified")).strip().lower() != "verified"
    ]
    verified_real_devices = [
        d for d in real_devices
        if str(d.get("verification_status", "")).strip().lower() == "verified"
    ]

    # 3. JSON Output Mode
    if args.json:
        report_data = {
            "as_of": today.isoformat(),
            "inventory_directory": str(inv_dir),
            "total_devices": total_devices,
            "real_devices_count": len(real_devices),
            "example_devices_count": len(example_devices),
            "unverified_registered_devices_count": len(unverified_real_devices),
            "verified_registered_devices_count": len(verified_real_devices),
            "total_registered_cost_usd": grand_registered_cost,
            "known_price_count": grand_known_price,
            "unknown_price_count": grand_unknown_price,
            "categories": category_summary,
            "warranty": {
                "in_warranty_count": len(in_warranty),
                "expiring_soon_subset_count": len(expiring_soon),
                "expired_count": len(expired_warranty),
                "unknown_count": len(unknown_warranty),
                "expiring_soon_items": [
                    {"device": item, "expiry": d.isoformat(), "days_remaining": days}
                    for item, d, days in expiring_soon
                ]
            },
            "maintenance_alerts": maintenance_alerts,
            "disposed_devices_count": disposed_count
        }
        print(json.dumps(report_data, ensure_ascii=False, indent=2))
        return 0

    # 4. Standard Console Output
    print("=" * 72)
    print(f" HOUSEHOLD ELECTRONICS & APPLIANCES STATUS REPORT (As of {today})")
    print("=" * 72)

    if total_devices == 0:
        print("\n⚠️  台账为空，未发现任何设备记录。")
        print("=" * 72)
        return 0

    # [1] Category Summary
    if len(verified_real_devices) == 0:
        section_title = "[1] 未核实设备登记分布 (Unverified Asset Registrations)"
        total_label = "TOTAL (未核实登记)"
    elif len(unverified_real_devices) == 0:
        section_title = "[1] 已核实设备登记分布 (Verified Asset Registrations)"
        total_label = "TOTAL (已核实登记)"
    else:
        section_title = "[1] 设备登记分布 (Asset Registrations - Mixed Verification)"
        total_label = "TOTAL (设备登记)"

    print(f"\n{section_title}")
    print(f"{'Category':<25} | {'Count':<6} | {'Registered Cost (USD)':<22}")
    print("-" * 60)
    for cat, info in sorted(category_summary.items()):
        print(f"{cat:<25} | {info['count']:<6} | ${info['registered_cost']:>18,.2f}")
    print("-" * 60)
    print(f"{total_label:<25} | {grand_real_count:<6} | ${grand_registered_cost:>18,.2f}")

    if grand_unknown_price > 0:
        print(f"  ℹ️  缺价登记: {grand_unknown_price} 件 (金额未知，未计入合计金额)")

    if len(verified_real_devices) == 0:
        print(f"\n  ℹ️  汇总: {grand_real_count} 条未核实设备登记，登记采购金额合计 ${grand_registered_cost:,.2f} USD")
    elif len(unverified_real_devices) == 0:
        print(f"\n  ℹ️  汇总: {grand_real_count} 条已核实设备登记，登记采购金额合计 ${grand_registered_cost:,.2f} USD")
    else:
        print(f"\n  ℹ️  汇总: {grand_real_count} 条设备登记（已核实 {len(verified_real_devices)} 件，未核实 {len(unverified_real_devices)} 件），登记采购金额合计 ${grand_registered_cost:,.2f} USD")

    if example_devices:
        grand_example_cost = sum(info["example_value"] for info in category_summary.values())
        print(f"  ℹ️  示例记录 (已隔离排除): {len(example_devices)} 件 (登记成本 ${grand_example_cost:,.2f}，不计入质保与提醒)")

    # [2] Warranty
    print("\n[2] 质保状态追踪 (Warranty Status Tracking)")
    if expiring_soon:
        print("  ⚠️  即将在 60 天内过保 (Expiring Soon):")
        for item, w_date, days in expiring_soon:
            print(f"     - {item}: 到期日 {w_date} (剩余 {days} 天)")
    else:
        print("  ✅ 暂无 60 天内即将过保的设备。")

    print(f"  ℹ️  保修期内设备: {len(in_warranty)} 件 (含临期 {len(expiring_soon)} 件) | 已出保设备: {len(expired_warranty)} 件 | 质保未知: {len(unknown_warranty)} 件")

    # [3] Maintenance Alerts
    print("\n[3] 耗材更换与维护预警 (Maintenance & Consumable Alerts)")
    if maintenance_alerts:
        for alert in maintenance_alerts:
            status_tag = "🔴 已经逾期" if alert["is_overdue"] else "🟡 即将到期"
            print(f"  {status_tag}: [{alert['device']}] -> {alert['task']}")
            print(f"      到期日: {alert['due_date']} (距今 {alert['days_to_due']} 天 | 预警窗口: {alert['alert_lead_days']}天)")
    else:
        print("  ✅ 近期 (按预警窗口) 无逾期或紧迫的维护/耗材更换项目。")

    # [4] Data Hygiene (Real computing devices only)
    print("\n[4] 计算与存储设备数据卫生 (Data Hygiene & Backup Health)")
    computing_devices = [d for d in real_devices if d.get("_category") == "computing_and_storage"]
    for cd in computing_devices:
        dh = cd.get("data_hygiene", {}) if isinstance(cd.get("data_hygiene"), dict) else {}
        bk_date = dh.get("last_backup_verified", "未核实")
        fv = dh.get("filevault_enabled") if dh.get("filevault_enabled") is not None else cd.get("filevault_enabled", "N/A")
        cloud = dh.get("cloud_sync") if dh.get("cloud_sync") is not None else cd.get("cloud_sync", "未配置")
        if not cloud or not str(cloud).strip():
            cloud = "未配置"
        sn_masked = mask_sn(cd.get("serial_number"))
        print(f"  • {cd.get('brand')} {cd.get('model')} [{sn_masked}]:")
        print(f"      FileVault: {fv} | 最近备份验证: {bk_date} (登记信息，恢复凭据待建立)")
        print(f"      云端同步策略: {cloud}")

    print("\n" + "=" * 72)
    print(" END OF STATUS REPORT")
    print("=" * 72)
    return 0

if __name__ == "__main__":
    sys.exit(main())
