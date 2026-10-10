#!/usr/bin/env python3
"""
inventory_io.py
Shared I/O, parsing, date normalization, and validation rules for HEM.
Zero-dependency with strict parsing, error detection, and schema enforcement.
"""

import sys
import os
import re
import datetime
import math
from pathlib import Path

try:
    import yaml
    class UniqueKeyLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            mapping = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if key in mapping:
                    raise ValueError(f"Duplicate YAML key: '{key}'")
                mapping[key] = self.construct_object(value_node, deep=deep)
            return mapping
except ImportError:
    yaml = None

ALLOWED_STATUSES = {
    "active",
    "maintenance",
    "stored",
    "sold",
    "recycled",
    "traded_in",
    "gifted",
    "retired",
}

DISPOSED_STATUSES = {"sold", "recycled", "traded_in", "gifted", "retired"}
ACTIVE_STATUSES = {"active", "maintenance", "stored"}

ALLOWED_RECORD_KINDS = {"real", "example"}
ALLOWED_VERIFICATION_STATUSES = {"unverified", "verified"}

REQUIRED_FIELDS = ["id", "brand", "model", "name", "status"]

def parse_inline_list(inner_str):
    items = []
    cur = []
    in_quote = False
    q_char = None
    for ch in inner_str:
        if ch in ('"', "'"):
            if not in_quote:
                in_quote = True
                q_char = ch
            elif q_char == ch:
                in_quote = False
            cur.append(ch)
        elif ch == ',' and not in_quote:
            items.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if in_quote:
        raise ValueError("Unterminated quote in inline list")
    if cur:
        items.append("".join(cur).strip())
    return [parse_scalar(it) for it in items if it]

def parse_scalar(val_str):
    v = val_str.strip()
    if not v:
        return ""
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        if len(v) >= 2:
            return v[1:-1]
        return ""
    if v.startswith('[') and v.endswith(']'):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return parse_inline_list(inner)
    if v.lower() == 'true':
        return True
    if v.lower() == 'false':
        return False
    if v.lower() in ('null', 'none', '~'):
        return None
    # Special floats to reject or preserve
    if v.lower() in ('nan', 'infinity', '+infinity', '-infinity', 'inf', '+inf', '-inf'):
        return v
    try:
        if '.' in v:
            f = float(v)
            if not math.isnan(f) and not math.isinf(f):
                return f
        else:
            return int(v)
    except ValueError:
        pass
    return v

def clean_comment_and_check_quotes(line, line_num):
    in_quotes = False
    quote_char = None
    comment_idx = -1
    for i, ch in enumerate(line):
        if ch in ('"', "'"):
            if not in_quotes:
                in_quotes = True
                quote_char = ch
            elif quote_char == ch:
                in_quotes = False
        elif ch == '#' and not in_quotes:
            comment_idx = i
            break
    if in_quotes:
        raise ValueError(f"Line {line_num}: Unterminated quote in YAML: {line.strip()!r}")
    if comment_idx != -1:
        return line[:comment_idx]
    return line

def parse_simple_yaml(file_path):
    """
    Strict indentation-based YAML parser for the required subset.
    Raises ValueError on malformed lines, unquoted syntax errors, or duplicate keys.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        raw_lines = f.readlines()

    lines = []
    for line_num, raw in enumerate(raw_lines, 1):
        cleaned = clean_comment_and_check_quotes(raw, line_num)
        if not cleaned.strip():
            continue
        indent = len(cleaned) - len(cleaned.lstrip(' '))
        lines.append((indent, cleaned.strip(), line_num))

    if not lines:
        return {}

    idx = 0

    def parse_block(min_indent):
        nonlocal idx
        if idx >= len(lines):
            return None

        indent, text, lnum = lines[idx]
        if text.startswith('- '):
            res_list = []
            while idx < len(lines):
                cur_indent, cur_text, cur_lnum = lines[idx]
                if cur_indent < min_indent:
                    break
                if not cur_text.startswith('- '):
                    break
                
                sub_text = cur_text[2:].strip()
                idx += 1
                if not sub_text:
                    item_val = parse_block(cur_indent + 1)
                    res_list.append(item_val)
                elif (sub_text.startswith('"') and sub_text.endswith('"')) or (sub_text.startswith("'") and sub_text.endswith("'")):
                    res_list.append(sub_text[1:-1])
                elif sub_text.startswith('[') and sub_text.endswith(']'):
                    res_list.append(parse_scalar(sub_text))
                elif ':' in sub_text:
                    k, v = sub_text.split(':', 1)
                    k = k.strip()
                    v = v.strip()
                    item_dict = {}
                    if v:
                        item_dict[k] = parse_scalar(v)
                    else:
                        item_dict[k] = parse_block(cur_indent + 2)
                    
                    dict_indent = cur_indent + 2
                    while idx < len(lines):
                        d_indent, d_text, d_lnum = lines[idx]
                        if d_indent < dict_indent or d_text.startswith('- '):
                            break
                        if ':' not in d_text:
                            raise ValueError(f"Line {d_lnum}: Malformed YAML line: '{d_text}'")
                        dk, dv = d_text.split(':', 1)
                        dk = dk.strip()
                        dv = dv.strip()
                        if dk in item_dict:
                            raise ValueError(f"Line {d_lnum}: Duplicate YAML key '{dk}'")
                        idx += 1
                        if dv:
                            item_dict[dk] = parse_scalar(dv)
                        else:
                            item_dict[dk] = parse_block(d_indent + 1)
                    res_list.append(item_dict)
                else:
                    res_list.append(parse_scalar(sub_text))
            return res_list
        else:
            res_dict = {}
            while idx < len(lines):
                cur_indent, cur_text, cur_lnum = lines[idx]
                if cur_indent < min_indent:
                    break
                if cur_text.startswith('- '):
                    break
                if ':' not in cur_text:
                    raise ValueError(f"Line {cur_lnum}: Malformed YAML line: '{cur_text}'")
                
                k, v = cur_text.split(':', 1)
                k = k.strip()
                v = v.strip()
                if k in res_dict:
                    raise ValueError(f"Line {cur_lnum}: Duplicate YAML key '{k}'")
                idx += 1
                if v:
                    res_dict[k] = parse_scalar(v)
                else:
                    res_dict[k] = parse_block(cur_indent + 1)
            return res_dict

    root = parse_block(0)
    return root or {}

def load_yaml(file_path):
    if yaml is not None:
        with open(file_path, "r", encoding="utf-8") as f:
            return yaml.load(f, Loader=UniqueKeyLoader)
    return parse_simple_yaml(file_path)

def to_date(val):
    """
    Normalizes input to strict datetime.date.
    Never returns datetime.datetime to prevent TypeError during subtraction.
    """
    if val is None:
        return None
    if isinstance(val, datetime.datetime):
        return val.date()
    if isinstance(val, datetime.date):
        return val
    if isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            return None
        try:
            return datetime.date.fromisoformat(val_str)
        except ValueError:
            return None
    return None

def validate_date_str(val_str, field_name, dev_id, allow_future=True, today=None):
    if val_str is None or str(val_str).strip() == "":
        return True, None, ""
    d = to_date(val_str)
    if d is None:
        return False, None, f"Device {dev_id}: invalid date format for '{field_name}' ({val_str!r}). Expected YYYY-MM-DD."
    if not allow_future and today is not None and d > today:
        return False, d, f"Device {dev_id}: '{field_name}' cannot be in the future ({d} > {today})."
    return True, d, ""

def validate_device(dev, file_name, seen_ids, seen_sns, today):
    errors = []
    if not isinstance(dev, dict):
        return [f"In {file_name}: device item must be a mapping/dict, got {type(dev).__name__}"]

    dev_id = dev.get("id")
    if not dev_id or not str(dev_id).strip():
        return [f"In {file_name}: device missing required non-empty 'id'"]
    dev_id = str(dev_id).strip()

    # ID format safety check (no path traversal, no slashes)
    if "/" in dev_id or "\\" in dev_id or ".." in dev_id:
        errors.append(f"In {file_name}: device id '{dev_id}' contains invalid path characters")

    # Check ID uniqueness
    if dev_id in seen_ids:
        errors.append(f"Duplicate device ID '{dev_id}' found in {file_name} (previously in {seen_ids[dev_id]})")
    else:
        seen_ids[dev_id] = file_name

    # Check required fields
    for rf in REQUIRED_FIELDS:
        val = dev.get(rf)
        if val is None or str(val).strip() == "":
            errors.append(f"Device {dev_id} in {file_name} missing required field '{rf}'")

    # Status check
    status = str(dev.get("status", "")).strip()
    if status and status not in ALLOWED_STATUSES:
        errors.append(f"Device {dev_id} has invalid status '{status}'. Must be one of {sorted(ALLOWED_STATUSES)}")

    # Record kind check
    rec_kind = dev.get("record_kind")
    if rec_kind is not None and str(rec_kind).strip() != "":
        rec_kind_str = str(rec_kind).strip().lower()
        if rec_kind_str not in ALLOWED_RECORD_KINDS:
            errors.append(f"Device {dev_id} has invalid record_kind '{rec_kind}'. Must be one of {sorted(ALLOWED_RECORD_KINDS)}")

    # Verification status check
    v_stat = dev.get("verification_status")
    if v_stat is not None and str(v_stat).strip() != "":
        v_stat_str = str(v_stat).strip().lower()
        if v_stat_str not in ALLOWED_VERIFICATION_STATUSES:
            errors.append(f"Device {dev_id} has invalid verification_status '{v_stat}'. Must be one of {sorted(ALLOWED_VERIFICATION_STATUSES)}")
        elif v_stat_str == "verified":
            s_refs = dev.get("source_refs")
            if not s_refs or not isinstance(s_refs, list) or not any(str(r).strip() for r in s_refs) or s_refs == ["user_declaration_unverified"]:
                errors.append(f"Device {dev_id} is marked 'verified' but lacks credible verified source_refs")

    # S/N check (optional, but if present must be unique)
    sn = dev.get("serial_number")
    if sn is not None and str(sn).strip():
        sn_str = str(sn).strip()
        if sn_str in seen_sns:
            errors.append(f"Duplicate Serial Number '{sn_str}' for {dev_id} in {file_name} (previously in {seen_sns[sn_str]})")
        else:
            seen_sns[sn_str] = dev_id

    # Purchase price check
    price = dev.get("purchase_price_usd")
    if price is not None and str(price).strip() != "":
        if isinstance(price, bool):
            errors.append(f"Device {dev_id} has invalid boolean price: {price}")
        else:
            price_str = str(price).strip()
            if price_str.lower() in ("nan", "infinity", "+infinity", "-infinity", "inf", "+inf", "-inf"):
                errors.append(f"Device {dev_id} has invalid NaN or Infinity purchase price: '{price}'")
            else:
                try:
                    price_f = float(price)
                    if math.isnan(price_f) or math.isinf(price_f):
                        errors.append(f"Device {dev_id} has invalid NaN/Infinity purchase price: {price_f}")
                    elif price_f < 0:
                        errors.append(f"Device {dev_id} has negative purchase price: {price_f}")
                except (ValueError, TypeError):
                    errors.append(f"Device {dev_id} has non-numeric purchase price: '{price}'")

    # Purchase date check
    p_date = dev.get("purchase_date")
    if p_date is not None and str(p_date).strip():
        ok, d, msg = validate_date_str(p_date, "purchase_date", dev_id, allow_future=False, today=today)
        if not ok:
            errors.append(msg)

    # Warranty expiry check
    w_date = dev.get("warranty_expiry")
    if w_date is not None and str(w_date).strip():
        ok, d, msg = validate_date_str(w_date, "warranty_expiry", dev_id, allow_future=True)
        if not ok:
            errors.append(msg)

    # Maintenance schedule check
    m_sched = dev.get("maintenance_schedule")
    if m_sched is not None:
        if not isinstance(m_sched, dict):
            errors.append(f"Device {dev_id} 'maintenance_schedule' must be a mapping, got {type(m_sched).__name__}")
        else:
            for task_name, task_info in m_sched.items():
                if not isinstance(task_info, dict):
                    errors.append(f"Device {dev_id} task '{task_name}' must be a mapping")
                    continue
                # interval_days: strictly positive integer, cannot be float or bool
                interval = task_info.get("interval_days")
                if interval is not None:
                    if isinstance(interval, bool):
                        errors.append(f"Device {dev_id} task '{task_name}' interval_days cannot be boolean: {interval}")
                    elif isinstance(interval, float):
                        errors.append(f"Device {dev_id} task '{task_name}' interval_days must be exact integer, got float: {interval}")
                    else:
                        s_int = str(interval).strip()
                        if "." in s_int or not re.match(r"^-?\d+$", s_int):
                            errors.append(f"Device {dev_id} task '{task_name}' interval_days must be exact integer, got: '{interval}'")
                        else:
                            try:
                                int_val = int(s_int)
                                if int_val <= 0:
                                    errors.append(f"Device {dev_id} task '{task_name}' interval_days must be positive int (>0), got {int_val}")
                            except ValueError:
                                errors.append(f"Device {dev_id} task '{task_name}' interval_days invalid integer: '{interval}'")

                # last_serviced
                ls_date = task_info.get("last_serviced")
                if ls_date is not None and str(ls_date).strip():
                    ok, d, msg = validate_date_str(ls_date, f"{task_name}.last_serviced", dev_id, allow_future=False, today=today)
                    if not ok:
                        errors.append(msg)

                # next_due
                nd_date = task_info.get("next_due")
                if nd_date is not None and str(nd_date).strip():
                    ok, d, msg = validate_date_str(nd_date, f"{task_name}.next_due", dev_id, allow_future=True)
                    if not ok:
                        errors.append(msg)
                else:
                    errors.append(f"Device {dev_id} task '{task_name}' missing required 'next_due'")

    return errors

def load_and_validate_inventory_dir(inventory_dir, today=None):
    """
    Central unified inventory loader and validator used by ALL tools.
    Returns: (ok: bool, errors: list[str], all_devices: list[dict], files_count: int, sn_count: int)
    """
    if today is None:
        today = datetime.date.today()

    inventory_dir = Path(inventory_dir)
    if not inventory_dir.is_dir():
        return False, [f"Inventory directory not found: {inventory_dir}"], [], 0, 0

    yaml_files = sorted(inventory_dir.glob("*.yaml")) + sorted(inventory_dir.glob("*.yml"))
    if not yaml_files:
        return True, [], [], 0, 0

    seen_ids = {}
    seen_sns = {}
    all_devices = []
    errors = []

    for yf in yaml_files:
        try:
            data = load_yaml(yf)
        except Exception as e:
            errors.append(f"File {yf.name} parse error: {e}")
            continue

        if not isinstance(data, dict):
            errors.append(f"File {yf.name} root must be a mapping, got {type(data).__name__}")
            continue

        cat = data.get("category")
        if not cat or not str(cat).strip():
            errors.append(f"File {yf.name} missing required non-empty 'category'")

        if "devices" not in data:
            errors.append(f"File {yf.name} missing required 'devices' section")
            continue

        devs = data.get("devices")
        if devs is None or not isinstance(devs, list):
            errors.append(f"File {yf.name} 'devices' must be a list, got {type(devs).__name__}")
            continue

        for dev in devs:
            dev_errors = validate_device(dev, yf.name, seen_ids, seen_sns, today)
            if dev_errors:
                errors.extend(dev_errors)
            if isinstance(dev, dict):
                dev["_category"] = str(cat).strip()
                dev["_source_file"] = yf.name
                all_devices.append(dev)

    ok = (len(errors) == 0)
    return ok, errors, all_devices, len(yaml_files), len(seen_sns)

def update_yaml_lines(lines, device_id, task_name, serviced_date, next_due_date):
    """
    Locates the target device block in YAML lines regardless of key ordering,
    and updates or inserts last_serviced and next_due under the target task.
    """
    dev_blocks = []
    in_devices = False
    dev_indent = None
    cur_start = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if stripped == "devices:":
            in_devices = True
            continue
        if in_devices:
            if indent == 0 and not stripped.startswith("-"):
                if cur_start is not None:
                    dev_blocks.append((cur_start, i))
                    cur_start = None
                in_devices = False
                continue
            if stripped.startswith("- "):
                if dev_indent is None:
                    dev_indent = indent
                if indent == dev_indent:
                    if cur_start is not None:
                        dev_blocks.append((cur_start, i))
                    cur_start = i
    if cur_start is not None:
        dev_blocks.append((cur_start, len(lines)))

    target_block = None
    for start, end in dev_blocks:
        block_lines = lines[start:end]
        for bl in block_lines:
            bl_s = bl.strip()
            # Match id: device_id or - id: device_id
            m = re.search(r"^(?:-\s+)?id:\s*[\"']?" + re.escape(device_id) + r"[\"']?\s*(?:#.*)?$", bl_s)
            if m:
                target_block = (start, end)
                break
        if target_block:
            break

    if not target_block:
        return False, lines

    start, end = target_block
    block_lines = list(lines[start:end])

    sched_idx = -1
    sched_indent = -1
    for idx, bl in enumerate(block_lines):
        bl_s = bl.strip()
        if bl_s == "maintenance_schedule:":
            sched_idx = idx
            sched_indent = len(bl) - len(bl.lstrip(" "))
            break

    if sched_idx == -1:
        return False, lines

    task_idx = -1
    task_indent = -1
    for idx in range(sched_idx + 1, len(block_lines)):
        bl = block_lines[idx]
        bl_s = bl.strip()
        if not bl_s or bl_s.startswith("#"):
            continue
        indent = len(bl) - len(bl.lstrip(" "))
        if indent <= sched_indent:
            break
        if bl_s.startswith(task_name + ":"):
            task_idx = idx
            task_indent = indent
            break

    if task_idx == -1:
        return False, lines

    task_end = len(block_lines)
    for idx in range(task_idx + 1, len(block_lines)):
        bl = block_lines[idx]
        bl_s = bl.strip()
        if not bl_s or bl_s.startswith("#"):
            continue
        indent = len(bl) - len(bl.lstrip(" "))
        if indent <= task_indent:
            task_end = idx
            break

    val_indent = task_indent + 2
    updated_ls = False
    updated_nd = False
    new_task_lines = []

    for idx in range(task_idx + 1, task_end):
        bl = block_lines[idx]
        bl_s = bl.strip()
        indent = len(bl) - len(bl.lstrip(" "))
        spaces = " " * indent
        if bl_s.startswith("last_serviced:"):
            new_task_lines.append(spaces + 'last_serviced: "' + str(serviced_date) + '"\n')
            updated_ls = True
        elif bl_s.startswith("next_due:"):
            new_task_lines.append(spaces + 'next_due: "' + str(next_due_date) + '"\n')
            updated_nd = True
        else:
            new_task_lines.append(bl)

    val_spaces = " " * val_indent
    if not updated_ls:
        new_task_lines.insert(0, val_spaces + 'last_serviced: "' + str(serviced_date) + '"\n')
    if not updated_nd:
        new_task_lines.append(val_spaces + 'next_due: "' + str(next_due_date) + '"\n')

    new_block = block_lines[:task_idx + 1] + new_task_lines + block_lines[task_end:]
    final_lines = lines[:start] + new_block + lines[end:]
    return True, final_lines

def update_yaml_maintenance_schedule(yaml_path, device_id, task_name, serviced_date, next_due_date, is_newer=True):
    """
    Safely and atomically updates maintenance schedule in YAML.
    Verifies that the target values actually changed before replacing the file.
    """
    if not is_newer:
        return True

    yaml_path = Path(yaml_path)
    with open(yaml_path, "r", encoding="utf-8") as f:
        orig_lines = f.readlines()

    ok, new_lines = update_yaml_lines(orig_lines, device_id, task_name, serviced_date, next_due_date)
    if not ok:
        return False

    tmp_path = yaml_path.with_name(f"{yaml_path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

        # Verification pass: ensure target fields are updated and data is valid
        verified = load_yaml(tmp_path)
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
