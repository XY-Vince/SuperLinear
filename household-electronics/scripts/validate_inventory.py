#!/usr/bin/env python3
"""
validate_inventory.py
Validates the structural integrity, required fields, date formats,
prices, maintenance schedules, and uniqueness of serial numbers and IDs
across all inventory files.
"""

import sys
import datetime
import argparse
from pathlib import Path

# Add script dir to path to import inventory_io
script_dir = Path(__file__).resolve().parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

import inventory_io as io

# Re-export for compatibility with tests and probes
yaml = io.yaml
load_yaml = io.load_yaml
parse_simple_yaml = io.parse_simple_yaml
to_date = io.to_date
ALLOWED_STATUSES = io.ALLOWED_STATUSES
REQUIRED_FIELDS = io.REQUIRED_FIELDS

def main():
    parser = argparse.ArgumentParser(description="Validate HEM inventory files.")
    parser.add_argument("--inventory-dir", help="Custom inventory directory path")
    parser.add_argument("--as-of", help="Reference date YYYY-MM-DD for date validations")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    inv_dir = Path(args.inventory_dir).resolve() if args.inventory_dir else (project_root / "inventory")

    today = datetime.date.today()
    if args.as_of:
        parsed_today = io.to_date(args.as_of)
        if parsed_today:
            today = parsed_today
        else:
            print(f"ERROR: Invalid --as-of date format: {args.as_of}. Expected YYYY-MM-DD", file=sys.stderr)
            return 1

    ok, errors, devices, num_files, num_sns = io.load_and_validate_inventory_dir(inv_dir, today=today)

    if not ok:
        print(f"--- Validating Inventory Files in {inv_dir} ---", file=sys.stderr)
        print(f"\nFAILED with {len(errors)} error(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"--- Validating Inventory Files in {inv_dir} ---")
    print("\n--- Validation Summary ---")
    print(f"Total YAML Files Checked: {num_files}")
    print(f"Total Devices Checked:    {len(devices)}")
    print(f"Unique Serial Numbers:    {num_sns}")
    print("\nALL INVENTORY CHECKS PASSED: Zero schema errors, zero duplicate IDs/SNs.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
