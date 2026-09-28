#!/usr/bin/env python3
"""
Daily Briefing Generator for Yale Free Food Discovery Engine
============================================================
Compiles verified campus events, discovery tracks, and user interests into
actionable, user-centric Daily Briefings across Markdown, JSON, and CSV formats.
"""

import os
import sys
import json
import csv
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Add current scripts directory to sys.path
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from event_store import EventStore, canonicalize_url

NY_TZ = ZoneInfo("America/New_York")


def clean_cell(val):
    if val is None:
        return "unknown"
    return str(val).replace("|", "/").replace("\n", " ").strip()


def render_briefing_markdown(result):
    meta = result["meta"]
    target_date = meta["target_date"]
    days = meta.get("days", 1)
    is_range = days > 1
    agenda = result.get("agenda", [])
    unresolved = result.get("unresolved_leads", [])
    changes = result.get("changes", [])

    lines = []
    if is_range:
        start_d = datetime.fromisoformat(target_date).date()
        end_d = start_d + timedelta(days=days - 1)
        half_open_end = start_d + timedelta(days=days)
        lines.append(f"# 📅 耶鲁校园活动与免费餐饮周报 ({start_d.strftime('%-m/%-d')}–{end_d.strftime('%-m/%-d')}，半开区间 [{start_d.isoformat()}, {half_open_end.isoformat()})，共 {days} 天)")
    else:
        lines.append(f"# 📅 耶鲁校园活动与免费餐饮日报 ({target_date})")
    lines.append("")
    scope_name = "周期概览" if is_range else "当日概览"
    cf_food = meta.get("confirmed_free_food_count", 0)
    free_food_claim = f"本次已核验结果中 {cf_food} 场明确免费餐饮（供餐provided／餐费free）" if cf_food > 0 else "本次已核验结果中暂无明确免费餐饮"
    lines.append(
        f"> **{scope_name}**：共 **{meta['agenda_count']}** 场精选日程，其中{free_food_claim}、供餐但餐费待核实 **{meta['food_provided_unknown_cost_count']}** 场、用户明确关注 **{meta.get('explicit_user_requests', meta.get('explicit_user_interests', 0))}** 场、自动发掘创新/社群推荐 **{meta.get('discovered_interest_events', 0)}** 场。"
    )
    lines.append(
        f"> **覆盖状态**：`{meta['coverage_status']}`（基于多源主动发现与日历爬虫；未包含全校私域封闭会议）。"
    )
    lines.append("")

    # Section 1: 🎯 优先行动 (Action Items)
    action_items = [
        e for e in agenda
        if "截止" in str(e.get("rsvp_status") or "") or "批准" in str(e.get("eligibility") or "") or e.get("user_requested")
    ]
    if action_items:
        lines.append("## 🎯 优先行动与报名提示")
        for item in action_items:
            t = item["title"]
            url = item.get("url")
            link_str = f"[{t}]({url})" if url else t
            note = item.get("participation_note") or item.get("rsvp_status") or "请留意报名与资格"
            lines.append(f"- **{link_str}**：{note}")
        lines.append("")

    rendered_ids = set()

    # Section 2: 🍔 明确免费餐饮 (Confirmed Free Food)
    confirmed_free = [
        e for e in agenda
        if e.get("food_status") == "provided" and e.get("food_cost") == "free" and e.get("confidence") == "confirmed_free"
    ]
    if confirmed_free:
        lines.append("## 🍔 明确免费餐饮日程")
        lines.append("| 美东时间 | 活动 | 餐食说明 | 地点与参加提示 |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for e in confirmed_free:
            rendered_ids.add(e.get("id"))
            dt = datetime.fromisoformat(e["starts_at"]).astimezone(NY_TZ)
            st = dt.strftime("%m/%d %H:%M" if is_range else "%H:%M")
            et = datetime.fromisoformat(e["ends_at"]).astimezone(NY_TZ).strftime("%H:%M") if e.get("ends_at") else "?"
            title = clean_cell(e["title"])
            link = f"[{title}]({e['url']})" if e.get("url") else title
            food_info = clean_cell(e.get("food_signals") or e.get("food_status"))
            loc_note = f"{clean_cell(e.get('location'))}；{clean_cell(e.get('participation_note') or e.get('rsvp_status'))}"
            lines.append(f"| {st}–{et} | {link} | {food_info} | {loc_note} |")
        lines.append("")

    # Section 3: 🥗 供餐但餐费未知 (Food Provided / Cost Unknown)
    provided_unknown = [
        e for e in agenda
        if e.get("id") not in rendered_ids and e.get("food_status") in ("provided", "likely") and e.get("food_cost") != "free" and not e.get("user_requested")
    ]
    if provided_unknown:
        lines.append("## 🥗 明确供餐 / 餐费待核实活动")
        lines.append("| 美东时间 | 活动 | 供餐状态 / 门票 | 地点与参加提示 |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for e in provided_unknown:
            rendered_ids.add(e.get("id"))
            dt = datetime.fromisoformat(e["starts_at"]).astimezone(NY_TZ)
            st = dt.strftime("%m/%d %H:%M" if is_range else "%H:%M")
            et = datetime.fromisoformat(e["ends_at"]).astimezone(NY_TZ).strftime("%H:%M") if e.get("ends_at") else "?"
            title = clean_cell(e["title"])
            link = f"[{title}]({e['url']})" if e.get("url") else title
            states = f"{clean_cell(e.get('food_status'))} / 门票: {clean_cell(e.get('admission_cost'))}"
            loc_note = f"{clean_cell(e.get('location'))}；{clean_cell(e.get('participation_note') or e.get('rsvp_status'))}"
            lines.append(f"| {st}–{et} | {link} | {states} | {loc_note} |")
        lines.append("")

    # Section 4: 💡 用户关注与精选科技社群活动 (User Interest / Community)
    user_interest = [
        e for e in agenda
        if e.get("id") not in rendered_ids and (e.get("user_requested") or e.get("record_kind") in ("user_interest", "interest_event"))
    ]
    if user_interest:
        lines.append("## 💡 用户关注与精选创业社群活动")
        lines.append("| 美东时间 | 活动 | 状态 | 地点与准入提示 |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for e in user_interest:
            rendered_ids.add(e.get("id"))
            if e.get("starts_at"):
                dt = datetime.fromisoformat(e["starts_at"]).astimezone(NY_TZ)
                st = dt.strftime("%m/%d %H:%M" if is_range else "%H:%M")
            else:
                st = "时间见通知"
            et = datetime.fromisoformat(e["ends_at"]).astimezone(NY_TZ).strftime("%H:%M") if e.get("ends_at") else "?"
            title = clean_cell(e["title"]) + (" ⭐" if e.get("user_requested") else "")
            link = f"[{title}]({e['url']})" if e.get("url") else title
            states = f"{clean_cell(e.get('category'))} / 供餐: {clean_cell(e.get('food_status'))}"
            loc_note = f"{clean_cell(e.get('location'))}；{clean_cell(e.get('participation_note') or e.get('rsvp_status'))}"
            lines.append(f"| {st}–{et} | {link} | {states} | {loc_note} |")
        lines.append("")

    # Section 5: 📌 其他校园日程与待确认状态活动 (Other Campus Events & Unclear Status)
    # Ensures every record in agenda is visible in markdown
    other_agenda = [
        e for e in agenda
        if e.get("id") not in rendered_ids
    ]
    if other_agenda:
        lines.append("## 📌 其他校园活动与待确认日程")
        lines.append("| 美东时间 | 活动 | 状态 / 餐饮 | 地点与参加提示 |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for e in other_agenda:
            rendered_ids.add(e.get("id"))
            if e.get("starts_at"):
                dt = datetime.fromisoformat(e["starts_at"]).astimezone(NY_TZ)
                st = dt.strftime("%m/%d %H:%M" if is_range else "%H:%M")
            else:
                st = "时间见通知"
            et = datetime.fromisoformat(e["ends_at"]).astimezone(NY_TZ).strftime("%H:%M") if e.get("ends_at") else "?"
            title = clean_cell(e["title"])
            link = f"[{title}]({e['url']})" if e.get("url") else title
            f_stat = clean_cell(e.get('food_status')) or "unknown"
            f_cost = clean_cell(e.get('food_cost')) or "unknown"
            states = f"{clean_cell(e.get('category'))} / 供餐: {f_stat} (餐费: {f_cost})"
            loc_note = f"{clean_cell(e.get('location'))}；{clean_cell(e.get('participation_note') or e.get('rsvp_status'))}"
            lines.append(f"| {st}–{et} | {link} | {states} | {loc_note} |")
        lines.append("")

    # Section 6: 📋 待核实线索队列 (Unverified Leads)
    if unresolved:
        lines.append("## 📋 待核实线索队列 (无明确日期/时区不明)")
        lines.append(f"共 **{len(unresolved)}** 条线索未设置具体开始时间，保留在线索库待后续解析：")
        for ld in unresolved[:10]:
            title = clean_cell(ld.get("title"))
            url = ld.get("url")
            link = f"[{title}]({url})" if url else title
            lines.append(f"- **{link}** (主办: {clean_cell(ld.get('host'))}, 供餐: {clean_cell(ld.get('food_status'))})")
        if len(unresolved) > 10:
            lines.append(f"- ... 及其余 {len(unresolved) - 10} 条线索")
        lines.append("")

    return "\n".join(lines) + "\n"


def generate_briefing(state_dir, target_date_str, days=1, snapshot_path=None, leads_paths=None, corrections_path=None, out_dir=None, extra_meta=None):
    store = EventStore(state_dir=state_dir)
    leads_paths = leads_paths or []

    # Import snapshot if provided
    snapshot_events = []
    snapshot_meta = {}
    if snapshot_path and Path(snapshot_path).exists():
        try:
            with open(snapshot_path, "r", encoding="utf-8") as f:
                snap_data = json.load(f)
            snapshot_meta = snap_data.get("meta", {})
            for ev in snap_data.get("events", []) + snap_data.get("unverified_food_leads", []):
                snapshot_events.append(ev)
                store.upsert_event(ev, method="snapshot_replay")
        except Exception as e:
            print(f"[WARN] Failed to load snapshot {snapshot_path}: {e}", file=sys.stderr)

    # Import lead files if provided
    for lp in leads_paths:
        p = Path(lp)
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    ldata = json.load(f)
                for ev in ldata.get("events", []) + ldata.get("unverified_food_leads", []):
                    store.upsert_event(ev, method="user_lead_import")
            except Exception as e:
                print(f"[WARN] Failed to load lead file {p}: {e}", file=sys.stderr)

    # Apply review corrections if provided
    if corrections_path and Path(corrections_path).exists():
        try:
            with open(corrections_path, "r", encoding="utf-8") as f:
                corrections = json.load(f)
            for c in corrections:
                cid = c.get("id")
                fields = c.get("fields", {})
                rec = {"id": cid}
                rec.update(fields)
                store.upsert_event(rec, method="review_correction")
        except Exception as e:
            print(f"[WARN] Failed to apply corrections {corrections_path}: {e}", file=sys.stderr)

    if days > 1:
        agenda, unresolved = store.get_events_for_range(target_date_str, days=days)
    else:
        agenda, unresolved = store.get_events_for_date(target_date_str)

    # Calculate metrics
    confirmed_free_count = sum(
        e.get("food_status") == "provided" and e.get("food_cost") == "free" and e.get("confidence") == "confirmed_free"
        for e in agenda
    )
    food_provided_unknown_count = sum(
        e.get("food_status") in ("provided", "likely") and e.get("food_cost") != "free"
        for e in agenda
    )
    explicit_user_count = sum(bool(e.get("user_requested")) for e in agenda)
    discovered_interest_count = sum(
        bool(not e.get("user_requested") and e.get("record_kind") in ("user_interest", "interest_event"))
        for e in agenda
    )

    sources = snapshot_meta.get("sources", [])
    incomplete = [
        s.get("id") for s in sources
        if s.get("run_status") != "outside_window" and (s.get("coverage") != "complete" or s.get("run_status") != "checked")
    ]
    coverage_status = "partial" if incomplete or not sources else "complete"

    result = {
        "meta": {
            "target_date": target_date_str,
            "days": days,
            "coverage_status": coverage_status,
            "coverage_scope": "multi-source dynamic discovery + listed sources",
            "agenda_count": len(agenda),
            "confirmed_free_food_count": confirmed_free_count,
            "food_provided_unknown_cost_count": food_provided_unknown_count,
            "explicit_user_requests": explicit_user_count,
            "discovered_interest_events": discovered_interest_count,
            "explicit_user_interests": explicit_user_count,
            "unresolved_leads_count": len(unresolved),
            "generated_at": datetime.now(timezone.utc).isoformat()
        },
        "agenda": agenda,
        "unresolved_leads": unresolved
    }
    if extra_meta and isinstance(extra_meta, dict):
        result["meta"].update(extra_meta)

    if out_dir:
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        md_content = render_briefing_markdown(result)
        prefix = "weekly" if days > 1 else "daily"
        (out_path / f"{prefix}-briefing.md").write_text(md_content, encoding="utf-8")
        (out_path / f"{prefix}-briefing.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        csv_fields = [
            "id", "title", "starts_at", "ends_at", "location", "host",
            "food_status", "food_cost", "admission_cost", "confidence",
            "user_requested", "rsvp_status", "participation_note", "url"
        ]
        with open(out_path / f"{prefix}-briefing.csv", "w", encoding="utf-8", newline="") as cf:
            writer = csv.DictWriter(cf, fieldnames=csv_fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(agenda)

        # Also write daily-briefing.* aliases if requested for backward compatibility
        if prefix != "daily" and not (out_path / "daily-briefing.md").exists():
            (out_path / "daily-briefing.md").write_text(md_content, encoding="utf-8")
            (out_path / "daily-briefing.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            with open(out_path / "daily-briefing.csv", "w", encoding="utf-8", newline="") as cf:
                writer = csv.DictWriter(cf, fieldnames=csv_fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(agenda)

        store.export_ledger(out_path / "discovery-ledger.jsonl")

    return result


def main():
    parser = argparse.ArgumentParser(description="Generate Yale Free Food & Events Daily Briefing")
    parser.add_argument("--date", required=True, help="Target date YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=1, help="Number of days (default: 1 for daily)")
    parser.add_argument("--state-dir", default=None, help="Path to state/cache directory")
    parser.add_argument("--snapshot", default=None, help="Path to baseline snapshot JSON")
    parser.add_argument("--leads", action="append", default=[], help="Path to user leads JSON")
    parser.add_argument("--corrections", default=None, help="Path to review corrections JSON")
    parser.add_argument("--out-dir", default=None, help="Output directory for briefing files")
    args = parser.parse_args()

    result = generate_briefing(
        state_dir=args.state_dir,
        target_date_str=args.date,
        days=args.days,
        snapshot_path=args.snapshot,
        leads_paths=args.leads,
        corrections_path=args.corrections,
        out_dir=args.out_dir
    )

    print(json.dumps(result["meta"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
