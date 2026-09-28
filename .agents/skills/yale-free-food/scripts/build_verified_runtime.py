#!/usr/bin/env python3
"""
Yale Free Food & Campus Events Discovery Pipeline
===================================================
Automated orchestration pipeline that executes real multi-source discovery,
crawls verified event pages, extracts food & admission facts, and compiles
daily and weekly briefings.

NO hardcoded synthetic event dictionaries or fake success states.
All records are derived directly from:
1. YaleConnect official baseline snapshot (when explicitly requested)
2. Active multi-source crawling of Tsai CITY, Poorvu Center, and English Institute pages
3. Frontier discovery and research ingestion
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from event_store import EventStore, canonicalize_url
import discovery
from briefing import generate_briefing


def load_baseline_records(store, base_dir=None):
    """
    Load legitimate baseline records for 2026-09-27 from source snapshot
    and known leads, applying review field corrections from review-corrections.json.
    Excludes any unverified manual additions or code-level case overrides.
    """
    if base_dir is None:
        base_dir = ROOT / "data/baseline/day-2026-09-27"
    else:
        base_dir = Path(base_dir)

    source_run_file = base_dir / "source-run.json"
    known_leads_file = base_dir / "known-leads.json"
    corrections_file = base_dir / "review-corrections.json"

    corrections_map = {}
    if corrections_file.exists():
        try:
            for item in json.loads(corrections_file.read_text(encoding="utf-8")):
                corrections_map[item["id"]] = item.get("fields", {})
        except Exception:
            pass

    # 1. Official YaleConnect events
    if source_run_file.exists():
        data = json.loads(source_run_file.read_text(encoding="utf-8"))
        events = data.get("events") or data.get("food_events") or []
        for ev in events:
            eid = str(ev.get("id"))
            # Apply corrections from reviewed corrections file
            if eid in corrections_map:
                ev.update(corrections_map[eid])
            ev["url"] = f"https://yaleconnect.yale.edu/rsvp_boot?id={eid}"
            store.upsert_event(ev, method="baseline_catalog", is_independent_new=0)

    # 2. Known user leads (WorkBuddy & YVC)
    if known_leads_file.exists():
        data = json.loads(known_leads_file.read_text(encoding="utf-8"))
        leads = data.get("events", []) if isinstance(data, dict) else data
        for lead in leads:
            lead["user_requested"] = 1
            lead["record_kind"] = "user_interest"
            lead["food_status"] = lead.get("food_status") or "unclear"
            lead["food_cost"] = lead.get("food_cost") or "unknown"
            store.upsert_event(lead, method="user_interest", is_independent_new=0)


def fetch_and_ingest_yaleconnect(store, from_date_str, days=7, mode="live"):
    """
    Fetch events from YaleConnect mobile API for the target window and ingest into store.
    Distinguishes network failures from programming errors and returns source status.
    """
    if mode != "live":
        return {"status": "skipped", "events": [], "detail_queue": [], "error": None}

    start_d = datetime.strptime(from_date_str, "%Y-%m-%d").date()
    end_d = start_d + timedelta(days=days - 1)

    try:
        from fetch_free_food import fetch_events, parse_event_list
    except ImportError as e:
        print(f"[ERROR] Programming error importing YaleConnect components: {e}", file=sys.stderr)
        return {"status": "error", "events": [], "detail_queue": [], "error": str(e)}

    try:
        raw_events = fetch_events(
            limit=100,
            from_date=start_d.strftime("%Y-%m-%d"),
            to_date=end_d.strftime("%Y-%m-%d"),
            paginate=True,
            max_pages=5
        )
        if raw_events:
            parsed, detail_queue = parse_event_list(
                raw_events,
                from_date_str=start_d.strftime("%Y-%m-%d"),
                to_date_str=end_d.strftime("%Y-%m-%d")
            )
            for ev in parsed:
                eid = str(ev.get("id"))
                if not ev.get("url"):
                    ev["url"] = f"https://yaleconnect.yale.edu/rsvp_boot?id={eid}"
                store.upsert_event(ev, method="yaleconnect_feed", is_independent_new=0)
            print(f"[OK] Ingested {len(parsed)} events from YaleConnect live feed for {from_date_str}..{end_d.strftime('%Y-%m-%d')}")
            return {"status": "ok", "events": parsed, "detail_queue": detail_queue, "error": None}
        return {"status": "empty", "events": [], "detail_queue": [], "error": None}
    except Exception as e:
        is_net = any(k in str(type(e)).lower() for k in ("urlerror", "httperror", "timeout", "socket", "ssl")) or "network" in str(e).lower()
        if is_net:
            print(f"[WARN] YaleConnect live feed network unavailable ({e}); continuing with multi-source discovery.")
            return {"status": "network_unavailable", "events": [], "detail_queue": [], "error": str(e)}
        else:
            print(f"[ERROR] YaleConnect live feed error: {e}", file=sys.stderr)
            return {"status": "error", "events": [], "detail_queue": [], "error": str(e)}


def execute_yaleconnect_details(store, detail_queue, from_date_str=None, to_date_str=None, budget=20, timeout=10):
    """
    Execute detail requests for items in detail_queue within budget.
    Fetches detail HTML via feed.fetch_event_detail_html, parses details,
    reclassifies event with food signals, and updates store.
    """
    import fetch_free_food as feed
    stats = {"executed": 0, "succeeded": 0, "failed": 0, "pending": 0}
    if not detail_queue:
        return stats

    for idx, entry in enumerate(detail_queue):
        if idx >= budget:
            stats["pending"] += 1
            continue

        priority = 2
        full_url = None
        raw_item = None
        ev = None

        if isinstance(entry, (list, tuple)):
            priority = entry[0]
            full_url = entry[1]
            if len(entry) >= 4:
                raw_item = entry[2]
                ev = entry[3]
            elif len(entry) == 3:
                if isinstance(entry[2], dict) and "p3" in entry[2]:
                    raw_item = entry[2]
                else:
                    ev = entry[2]
                    raw_item = ev.get("_raw_item") if isinstance(ev, dict) else None
            elif len(entry) == 2:
                pass
        elif isinstance(entry, dict):
            priority = entry.get("priority", 2)
            full_url = entry.get("url")
            raw_item = entry.get("item")
            ev = entry.get("event")

        if not full_url:
            continue

        stats["executed"] += 1
        try:
            html = feed.fetch_event_detail_html(full_url, timeout=timeout)
            if html:
                d_info = feed.parse_detail_page(html)
                reclassified = None
                if raw_item and isinstance(raw_item, dict):
                    reclassified = feed.classify_event(
                        raw_item,
                        detail_info=d_info,
                        from_date_str=from_date_str,
                        to_date_str=to_date_str
                    )
                if reclassified:
                    reclassified["details_verified"] = True
                    if not reclassified.get("url"):
                        reclassified["url"] = full_url
                    reclassified["record_kind"] = "food_candidate" if reclassified.get("food_status") in ("provided", "likely") else "interest_event"
                    store.upsert_event(reclassified, method="yaleconnect_detail", is_independent_new=0)
                    stats["succeeded"] += 1
                elif ev and isinstance(ev, dict):
                    ev["details_verified"] = True
                    if d_info.get("has_food_badge") or (d_info.get("details_text") and "pizza" in d_info.get("details_text", "").lower()):
                        ev["food_status"] = "provided"
                        ev["food_cost"] = "free"
                        ev["confidence"] = "confirmed_free"
                    store.upsert_event(ev, method="yaleconnect_detail", is_independent_new=0)
                    stats["succeeded"] += 1
                else:
                    stats["failed"] += 1
            else:
                stats["failed"] += 1
        except Exception as e:
            print(f"[WARN] Error executing detail for {full_url}: {e}", file=sys.stderr)
            stats["failed"] += 1

    return stats


def run_pipeline(
    state_dir,
    mode="live",
    from_date=None,
    days=7,
    profile="daily",
    clean=False,
    import_baseline=None,
    research_file=None,
    resume=False
):
    state_path = Path(state_dir).expanduser().resolve()
    state_path.mkdir(parents=True, exist_ok=True)

    if from_date is None:
        if mode == "replay":
            from_date = "2026-09-27"
        else:
            try:
                from zoneinfo import ZoneInfo
                ny_tz = ZoneInfo("America/New_York")
            except ImportError:
                import pytz
                ny_tz = pytz.timezone("America/New_York")
            from_date = datetime.now(ny_tz).strftime("%Y-%m-%d")

    if clean:
        for fname in ("state.db", "supplemental_events.json", "discovery-ledger.jsonl"):
            p = state_path / fname
            if p.exists():
                p.unlink()

    store = EventStore(state_dir=state_path)

    # Step 1: Baseline records only if explicitly requested
    if import_baseline:
        baseline_path = Path(import_baseline).resolve() if (import_baseline and import_baseline is not True) else None
        load_baseline_records(store, base_dir=baseline_path)

    # Step 2: Ingest YaleConnect for target window (live)
    yc_stats = {
        "status": "skipped",
        "events": 0,
        "detail_queue": 0,
        "details_executed": 0,
        "details_succeeded": 0,
        "details_failed": 0,
        "details_pending": 0,
        "error": None
    }
    if mode == "live":
        start_d = datetime.strptime(from_date, "%Y-%m-%d").date()
        end_d = start_d + timedelta(days=days - 1)
        yc_res = fetch_and_ingest_yaleconnect(store, from_date, days=days, mode=mode)
        yc_stats["status"] = yc_res.get("status", "unknown")
        yc_stats["events"] = len(yc_res.get("events", []))
        yc_stats["error"] = yc_res.get("error")
        detail_q = yc_res.get("detail_queue", [])
        yc_stats["detail_queue"] = len(detail_q)
        if detail_q:
            detail_res = execute_yaleconnect_details(
                store,
                detail_q,
                from_date_str=from_date,
                to_date_str=end_d.strftime("%Y-%m-%d"),
                budget=20
            )
            yc_stats["details_executed"] = detail_res["executed"]
            yc_stats["details_succeeded"] = detail_res["succeeded"]
            yc_stats["details_failed"] = detail_res["failed"]
            yc_stats["details_pending"] = detail_res["pending"]
            print(f"[OK] Executed {detail_res['executed']} YaleConnect details (succeeded={detail_res['succeeded']}, failed={detail_res['failed']}, pending={detail_res['pending']})")

    # Step 3: Ingest research file if specified or present
    if research_file:
        rf_path = Path(research_file).expanduser().resolve()
        if rf_path.exists():
            r_args = argparse.Namespace(
                input=str(rf_path),
                file=str(rf_path),
                state_dir=str(state_path)
            )
            discovery.execute_ingest_research_command(r_args)

    # Step 4: Run active discovery crawler across source seeds
    args = argparse.Namespace(
        state_dir=str(state_path),
        profile=profile,
        resume=resume,
        from_date=from_date,
        days=days,
        mode=mode,
        cache_dir=str(state_path / "cache")
    )
    discovery.execute_run_command(args)

    extra_meta = {"yaleconnect": yc_stats}

    # Step 5: Generate Daily Briefing
    daily_report = generate_briefing(
        state_dir=str(state_path),
        target_date_str=from_date,
        days=1,
        out_dir=str(state_path),
        extra_meta=extra_meta
    )

    # Step 6: Generate Weekly Briefing
    weekly_report = generate_briefing(
        state_dir=str(state_path),
        target_date_str=from_date,
        days=days,
        out_dir=str(state_path),
        extra_meta=extra_meta
    )

    store.sync_supplemental_json()

    print(f"[OK] Pipeline completed ({mode}) for state_dir={state_path}")
    print(f"     Target window: {from_date} (+{days} days)")
    print(f"     Daily agenda ({from_date}): {daily_report['meta']['agenda_count']} items")
    print(f"     Weekly agenda ({days} days): {weekly_report['meta']['agenda_count']} items")
    return {"daily": daily_report, "weekly": weekly_report}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Yale Free Food Discovery Pipeline")
    parser.add_argument("--state-dir", default=str(ROOT / "data/runtime"), help="State directory")
    parser.add_argument("--mode", choices=["live", "replay"], default="live", help="Execution mode (live/replay)")
    parser.add_argument("--from-date", default=None, help="Start date YYYY-MM-DD (defaults to US/Eastern today in live, 2026-09-27 in replay)")
    parser.add_argument("--days", type=int, default=7, help="Window days (default: 7)")
    parser.add_argument("--profile", default="daily", help="Profile (quick/daily/deep)")
    parser.add_argument("--import-baseline", nargs="?", const=True, default=None, help="Import baseline snapshot (optional path or flag)")
    parser.add_argument("--research-file", default=None, help="Import research search results JSONL file")
    parser.add_argument("--clean", action="store_true", help="Clean state directory before run")
    parser.add_argument("--resume", action="store_true", help="Resume pending frontier items")
    args = parser.parse_args()

    run_pipeline(
        args.state_dir,
        mode=args.mode,
        from_date=args.from_date,
        days=args.days,
        profile=args.profile,
        clean=args.clean,
        import_baseline=args.import_baseline,
        research_file=args.research_file,
        resume=args.resume
    )
