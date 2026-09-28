#!/usr/bin/env python3
"""
Yale Free Food & Campus Events Discovery Engine
=================================================
Automated multi-source query planner, research ingester, and recursive crawler.
Implements 2-hop host expansion, persistent frontier queue, and profile budgets.
"""

import os
import sys
import json
import re
import time
import hashlib
import argparse
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, urljoin, parse_qs, urlencode
from zoneinfo import ZoneInfo

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from event_store import EventStore, canonicalize_url
from ingest_multi_source import (
    parse_luma_html,
    parse_eventbrite_html,
    fetch_url_html,
    evaluate_food_classification,
    get_ny_timezone,
    clean_text,
    InvalidEventError,
    NetworkFetchError,
)
from fetch_free_food import analyze_food_text

NY_TZ = ZoneInfo("America/New_York")


def load_seeds():
    seed_file = _SCRIPTS_DIR.parent / "references" / "source-seeds.json"
    if seed_file.exists():
        try:
            with open(seed_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def generate_query_plan(from_date_str, days=7, profile="daily"):
    """
    Generate structured search queries from 6 query families.
    Ensures at least 50% of the queries are non-food queries to discover
    general innovation, tech, workshop, and community events.
    """
    start_d = datetime.strptime(from_date_str, "%Y-%m-%d").date()
    end_d = start_d + timedelta(days=days - 1)
    month_name = start_d.strftime("%B")
    month_short = start_d.strftime("%b")
    year_str = str(start_d.year)
    day_str = f"{start_d.month}/{start_d.day}"

    # F1: Official Yale Calendar / Events (with food / reception terms)
    f1 = [
        f"site:yale.edu (events OR calendar) (reception OR refreshments OR lunch OR welcome) {month_name} {year_str}",
        f"site:yale.edu/events {from_date_str}",
        f"site:city.yale.edu/events {month_short} {year_str}",
    ]

    # F2: Tech, Innovation, Founders, Workshops (NON-FOOD queries - 50% quota)
    f2 = [
        f'(Yale OR "New Haven") (workshop OR demo OR founder OR networking OR symposium) {from_date_str}',
        f'(Yale OR "New Haven") (hackathon OR tech talk OR AI OR seminar) {month_name} {year_str}',
        f'site:seas.yale.edu/news-events/events {month_short} {year_str}',
        f'site:som.yale.edu/event {month_short} {year_str}',
    ]

    # F3: Lu.ma New Haven / Yale community (Mix of food and non-food)
    f3 = [
        f'site:luma.com (Yale OR "New Haven") {month_name} {year_str}',
        f'site:lu.ma (Yale OR "New Haven") {month_short} {year_str}',
        f'site:luma.com/TheYaleTable',
    ]

    # F4: Eventbrite public campus / New Haven events
    f4 = [
        f'site:eventbrite.com/e/ ("Yale" OR "New Haven") {month_short} {year_str}',
        f'site:eventbrite.com/d/ct--new-haven/free--events/ {month_name}',
    ]

    # F5: Free Food / Refreshments explicitly
    f5 = [
        f'(Yale OR "New Haven") ("free snacks" OR "lunch provided" OR complimentary OR reception) {month_name} {year_str}',
        f'(Yale OR "New Haven") ("pizza provided" OR "coffee and pastries" OR "catered reception") {from_date_str}',
    ]

    # F6: Chinese & Student Community (ACSSYale / YVC / etc.)
    f6 = [
        f'site:mp.weixin.qq.com (耶鲁 OR Yale) (活动 OR 创投 OR 学联 OR 聚会) {year_str}',
        f'(耶鲁 OR 纽黑文) (迎新 OR 讲座 OR 茶歇 OR 聚会) {start_d.month}月',
    ]

    tasks = []
    # Interleave queries, tagging family and non-food flags
    for q in f2:
        tasks.append({"task_id": f"q_f2_{len(tasks)+1:02d}", "family": "F2_innovation_nonfood", "query": q, "has_food_keyword": False, "priority": 10})
    for q in f1:
        tasks.append({"task_id": f"q_f1_{len(tasks)+1:02d}", "family": "F1_campus_official", "query": q, "has_food_keyword": True, "priority": 8})
    for q in f3:
        tasks.append({"task_id": f"q_f3_{len(tasks)+1:02d}", "family": "F3_luma_platform", "query": q, "has_food_keyword": False, "priority": 9})
    for q in f4:
        tasks.append({"task_id": f"q_f4_{len(tasks)+1:02d}", "family": "F4_eventbrite", "query": q, "has_food_keyword": False, "priority": 7})
    for q in f5:
        tasks.append({"task_id": f"q_f5_{len(tasks)+1:02d}", "family": "F5_free_food_explicit", "query": q, "has_food_keyword": True, "priority": 10})
    for q in f6:
        tasks.append({"task_id": f"q_f6_{len(tasks)+1:02d}", "family": "F6_chinese_community", "query": q, "has_food_keyword": False, "priority": 8})

    # Profile sizing
    if profile == "quick":
        tasks = tasks[:8]
    elif profile == "daily":
        tasks = tasks[:18]
    # 'deep' keeps all generated queries

    seeds = load_seeds()
    crawl_tasks = [
        {
            "task_id": f"crawl_{s['source_id']}",
            "source_id": s["source_id"],
            "url": s["entry_url"],
            "family": s.get("family", "seed"),
            "priority": 15
        }
        for s in seeds
    ]

    return {
        "meta": {
            "from_date": from_date_str,
            "to_date": end_d.isoformat(),
            "days": days,
            "profile": profile,
            "search_tasks_count": len(tasks),
            "non_food_queries_count": sum(1 for t in tasks if not t["has_food_keyword"]),
            "crawl_tasks_count": len(crawl_tasks),
            "generated_at": datetime.now(timezone.utc).isoformat()
        },
        "search_tasks": tasks,
        "seed_crawl_tasks": crawl_tasks
    }


def execute_plan_command(args):
    plan = generate_query_plan(args.from_date, args.days, args.profile)
    store = EventStore(state_dir=args.state_dir)

    # Initialize frontier with seeds
    for ct in plan["seed_crawl_tasks"]:
        store.enqueue_url(ct["url"], discovery_parent="seed_catalog", priority=ct["priority"], depth=0)

    # Sync sources catalog into SQLite
    seeds = load_seeds()
    with store._get_conn() as conn:
        for s in seeds:
            conn.execute("""
            INSERT INTO sources (source_id, entry_url, family, adapter, discovery_parent, status, date_capability, seasonality)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                entry_url=excluded.entry_url,
                family=excluded.family,
                adapter=excluded.adapter,
                date_capability=excluded.date_capability,
                seasonality=excluded.seasonality
            """, (
                s["source_id"], s["entry_url"], s.get("family", ""),
                s.get("adapter", ""), s.get("discovery_parent", ""),
                s.get("status", "operational"), s.get("date_capability", ""),
                s.get("seasonality", "")
            ))

    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print(f"=== Discovery Plan ({plan['meta']['from_date']} to {plan['meta']['to_date']}) [Profile: {args.profile}] ===")
        print(f"Search Tasks: {plan['meta']['search_tasks_count']} (Non-food: {plan['meta']['non_food_queries_count']}) | Seeds Enqueued: {plan['meta']['crawl_tasks_count']}")
        print("\n--- Search Query Families ---")
        for st in plan["search_tasks"]:
            tag = "[NON-FOOD]" if not st["has_food_keyword"] else "[FOOD]"
            print(f"  [{st['family']}] {tag} {st['query']}")
        print("\n--- Seed Entry Crawls ---")
        for ct in plan["seed_crawl_tasks"]:
            print(f"  [{ct['source_id']}] {ct['url']}")


def parse_jsonld_event(html, url=None):
    """
    Extract Event from generic Schema.org JSON-LD scripts in HTML.
    """
    if not html:
        return None
    scripts = re.findall(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>', html, re.DOTALL)
    for s in scripts:
        try:
            parsed = json.loads(s)
            items = []
            if isinstance(parsed, dict):
                if parsed.get("@type") == "Event":
                    items.append(parsed)
                elif "@graph" in parsed and isinstance(parsed["@graph"], list):
                    items.extend([x for x in parsed["@graph"] if isinstance(x, dict) and x.get("@type") == "Event"])
            elif isinstance(parsed, list):
                items.extend([x for x in parsed if isinstance(x, dict) and x.get("@type") == "Event"])

            for ev_data in items:
                title = ev_data.get("name") or ev_data.get("title")
                starts_at = ev_data.get("startDate")
                ends_at = ev_data.get("endDate")
                desc = ev_data.get("description", "")
                if not title or not starts_at:
                    continue
                loc = "Yale Campus"
                if isinstance(ev_data.get("location"), dict):
                    loc = ev_data["location"].get("name") or ev_data["location"].get("address", {}).get("streetAddress", "Yale Campus")
                elif isinstance(ev_data.get("location"), str):
                    loc = ev_data["location"]

                analyzed = analyze_food_text(title, desc)
                f_status = analyzed["food_status"] if analyzed else "unclear"
                f_cost = analyzed["food_cost"] if analyzed else "unknown"
                conf = analyzed["confidence"] if analyzed else "needs_verification"

                return {
                    "id": f"disc:{hashlib.sha256((url or title).encode()).hexdigest()[:12]}",
                    "url": url or "",
                    "title": title,
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "dates": "",
                    "location": loc,
                    "host": ev_data.get("organizer", {}).get("name", "Yale Department") if isinstance(ev_data.get("organizer"), dict) else "Yale Department",
                    "category": "Department/Academic",
                    "food_status": f_status,
                    "food_cost": f_cost,
                    "admission_cost": analyzed.get("admission_cost", "free") if analyzed else "free",
                    "confidence": conf,
                    "record_kind": "food_candidate" if f_status in ("provided", "likely") else "interest_event",
                    "details_verified": 1,
                    "discovery_method": "official_jsonld",
                    "discovery_parent": "",
                    "evidence_text": desc,
                    "evidence": [desc] if desc else []
                }
        except Exception:
            continue

    # Fallback: Extract from HTML event cards (e.g. Poorvu Center, Drupal views reference-cards)
    cards = extract_html_event_cards(html, base_url=url)
    if cards:
        return cards[0]

    return None


def extract_html_event_cards(html, base_url=None):
    """
    Extract events from HTML card structures (e.g. Poorvu Center, Drupal views reference-cards).
    Returns a list of event dictionaries.
    """
    if not html:
        return []
    import html as html_lib
    results = []
    pattern = re.compile(
        r'<h[234][^>]*>\s*<a[^>]+href=[\"\']([^\"\'>]+)[\"\'][^>]*>(.*?)</a>\s*</h[234]>.*?'
        r'<time[^>]+datetime=[\"\']([^\"\'>]+)[\"\'][^>]*>(.*?)</time>',
        re.DOTALL | re.I
    )
    for m in pattern.finditer(html):
        href = m.group(1).strip()
        raw_title = html_lib.unescape(clean_text(m.group(2)))
        starts_at = m.group(3).strip()
        time_text = html_lib.unescape(clean_text(m.group(4)))
        if not raw_title or not starts_at:
            continue

        full_url = urljoin(base_url or "https://poorvucenter.yale.edu", href)
        ends_at = None
        if "—" in time_text:
            parts = time_text.split("—")
            try:
                end_str = parts[1].strip()
                s_dt = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
                m_end = re.search(r'(\d{1,2}):(\d{2})\s*(am|pm)', end_str, re.I)
                if m_end:
                    hr = int(m_end.group(1))
                    mn = int(m_end.group(2))
                    ampm = m_end.group(3).lower()
                    if ampm == "pm" and hr != 12:
                        hr += 12
                    elif ampm == "am" and hr == 12:
                        hr = 0
                    e_dt = s_dt.replace(hour=hr, minute=mn)
                    ends_at = e_dt.isoformat()
            except Exception:
                pass

        analyzed = analyze_food_text(raw_title, f"{raw_title} {time_text}")
        f_status = analyzed["food_status"] if analyzed else "unclear"
        f_cost = analyzed["food_cost"] if analyzed else "unknown"
        conf = analyzed["confidence"] if analyzed else "needs_verification"

        evidence_str = time_text

        card_host = "Campus Organization"
        card_location = "Check event page"
        if base_url:
            if "poorvucenter" in base_url.lower():
                card_host = "Poorvu Center for Teaching and Learning"
                card_location = "Poorvu Center / Check event page"
            elif "city.yale.edu" in base_url.lower():
                card_host = "Tsai CITY"
                card_location = "17 Prospect St, New Haven, CT (Tsai CITY)"

        results.append({
            "id": f"disc:{hashlib.sha256(full_url.encode()).hexdigest()[:12]}",
            "url": full_url,
            "title": raw_title,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "dates": time_text,
            "location": card_location,
            "host": card_host,
            "category": "Academic/Campus",
            "food_status": f_status,
            "food_cost": f_cost,
            "admission_cost": "unknown",
            "confidence": conf,
            "record_kind": "food_candidate" if f_status in ("provided", "likely") else "interest_event",
            "details_verified": 0,
            "discovery_method": "official_cards",
            "discovery_parent": base_url or "",
            "evidence_text": evidence_str,
            "evidence": [evidence_str] if evidence_str else []
        })

    return results


def execute_ingest_research_command(args):
    input_val = getattr(args, "input", None) or getattr(args, "file", None)
    if not input_val:
        print("[ERROR] Input research file not specified.", file=sys.stderr)
        return
    inp_path = Path(input_val).expanduser().resolve()
    if not inp_path.exists():
        print(f"[ERROR] Input research file not found: {inp_path}", file=sys.stderr)
        return

    store = EventStore(state_dir=args.state_dir)
    added = 0
    updated = 0
    enqueued_hosts = 0

    with open(inp_path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rec = json.loads(line)
            except Exception as e:
                print(f"[WARN] Skipping line {line_no} due to JSON parse error: {e}", file=sys.stderr)
                continue

            # Field normalization
            url = rec.get("result_url") or rec.get("url") or ""
            canon = canonicalize_url(url)
            title = rec.get("title") or "Untitled Event"
            parent_url = rec.get("query_or_parent_url") or rec.get("discovery_parent") or ""
            method = rec.get("extraction_method") or ("web_search" if "q_" in str(rec.get("task_id", "")) else "organizer_expansion")
            evidence = rec.get("evidence_text") or ""
            is_indep = bool(rec.get("is_independent_new", False))

            # Re-classify food if needed
            food_status = rec.get("food_status")
            food_cost = rec.get("food_cost")
            admission_cost = rec.get("admission_cost")
            confidence = rec.get("confidence")

            # Validate food claims against textual evidence (title and evidence text)
            if evidence or title:
                analyzed = analyze_food_text(title, evidence, admission_cost_explicit=admission_cost)
                if analyzed:
                    if food_status in ("provided", "likely") and analyzed["food_status"] == "unclear":
                        pass
                    else:
                        food_status = analyzed["food_status"]
                    food_cost = analyzed["food_cost"]
                    confidence = analyzed["confidence"]
                    if not admission_cost:
                        admission_cost = analyzed["admission_cost"]
                else:
                    food_status = "none" if "no food" in evidence.lower() else (food_status or "unclear")
                    food_cost = "unknown"
                    confidence = "needs_verification"

            record_kind = rec.get("record_kind")
            if not record_kind:
                if rec.get("user_requested") or rec.get("user_interest"):
                    record_kind = "user_interest"
                elif rec.get("starts_at") and food_status in ("provided", "likely"):
                    record_kind = "food_candidate"
                elif not rec.get("starts_at"):
                    record_kind = "unverified_food_lead"
                else:
                    record_kind = "interest_event"

            ev_payload = {
                "id": rec.get("id"),
                "url": url,
                "title": title,
                "starts_at": rec.get("starts_at"),
                "ends_at": rec.get("ends_at"),
                "dates": rec.get("dates") or "",
                "location": rec.get("location") or "unknown",
                "host": rec.get("host") or "unknown",
                "category": rec.get("category") or "Campus/Innovation",
                "food_status": food_status,
                "food_cost": food_cost or "unknown",
                "admission_cost": admission_cost or "unknown",
                "confidence": confidence or "needs_verification",
                "record_kind": record_kind,
                "user_requested": 1 if rec.get("user_requested") else 0,
                "interest_match": 1 if rec.get("user_interest") else 0,
                "fetch_mode": rec.get("fetch_mode") or "live",
                "cached_at": rec.get("cached_at"),
                "rsvp_status": rec.get("rsvp_status") or "unknown",
                "eligibility": rec.get("eligibility") or "unknown",
                "participation_note": rec.get("participation_note") or "",
                "discovery_method": method,
                "discovery_parent": parent_url,
                "details_verified": 1 if bool(rec.get("details_verified", False)) else 0
            }
            if evidence:
                ev_payload["evidence_text"] = evidence
                ev_payload["evidence"] = [evidence]

            store.upsert_event(ev_payload, method=method, parent_url=parent_url, is_independent_new=is_indep)
            added += 1

            # Check if host or parent URL leads to 2-hop expansion
            host_url = rec.get("host_url") or rec.get("parent_calendar_url")
            if host_url:
                c_host = canonicalize_url(host_url)
                if c_host and not store.get_frontier_item(c_host):
                    store.enqueue_url(host_url, discovery_parent=url, priority=12, depth=1)
                    enqueued_hosts += 1

    store.sync_supplemental_json()
    print(f"[OK] Ingested research results: {added} events upserted, {enqueued_hosts} host discovery entries enqueued.")


def crawl_page_for_links(html, base_url):
    """
    Extract relevant event and calendar links from page HTML for 2-hop discovery.
    """
    import html as html_lib
    links = set()
    for m in re.finditer(r'href=[\"\']([^\"\'>]+)[\"\']', html, re.I):
        href = m.group(1).strip()
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        href = html_lib.unescape(href)
        full_url = urljoin(base_url, href)
        u = urlsplit(full_url)
        # Match Lu.ma, Eventbrite, or Yale events
        if "luma.com" in u.netloc or "lu.ma" in u.netloc:
            # Avoid generic luma root or settings
            p = u.path.strip("/")
            if len(p.split("/")) >= 1 and p not in ("signin", "explore", "pricing", "create"):
                links.add(full_url)
        elif "eventbrite.com" in u.netloc and "/e/" in u.path:
            clean_eb = f"{u.scheme}://{u.netloc}{u.path.rstrip('/')}"
            links.add(clean_eb)
        elif u.netloc.endswith(".yale.edu") and any(k in u.path.lower() for k in ("event", "calendar")):
            links.add(full_url)
    return list(links)


def execute_run_command(args):
    store = EventStore(state_dir=args.state_dir)
    profile = args.profile or "daily"
    mode = getattr(args, "mode", "live")
    cache_dir = getattr(args, "cache_dir", None) or os.environ.get("YFF_CACHE_DIR") or (Path(args.state_dir) / "cache")
    budgets = {
        "quick": 15,
        "daily": 50,
        "deep": 200
    }
    max_requests = budgets.get(profile, 50)
    requests_made = 0
    discovered_events = 0

    print(f"[*] Starting Discovery Runner (Profile: {profile}, Mode: {mode}, Max Requests: {max_requests}, Resume: {args.resume})")

    # Ensure source seeds are always enqueued into frontier if not already present
    seeds = load_seeds()
    for s in seeds:
        c_entry = canonicalize_url(s["entry_url"])
        if c_entry and not store.get_frontier_item(c_entry):
            store.enqueue_url(s["entry_url"], discovery_parent="seed_catalog", priority=10, depth=0)

    while requests_made < max_requests:
        batch = store.get_pending_frontier(limit=5)
        if not batch:
            print("[*] Frontier queue empty. Discovery cycle completed.")
            break

        for item in batch:
            if requests_made >= max_requests:
                break
            url = item["url"]
            depth = item["depth"]
            parent_url = item["discovery_parent"]

            print(f"[{requests_made+1}/{max_requests}] Fetching (depth={depth}): {url}")
            requests_made += 1

            try:
                html = fetch_url_html(url, timeout=12, cache_dir=cache_dir, mode=mode)
            except Exception as e:
                store.mark_frontier_status(url, "failed", error_msg=str(e))
                continue

            fetch_mode = getattr(html, "mode", mode)
            cached_at = getattr(html, "cached_at", None)

            # Check for HTML event cards on listing pages (e.g. Poorvu Center)
            cards = extract_html_event_cards(html, base_url=url)
            if cards:
                from_date_str = getattr(args, "from_date", None) or "2026-09-27"
                days_win = getattr(args, "days", None) or 7
                win_start = datetime.fromisoformat(from_date_str).date()
                win_end = win_start + timedelta(days=days_win)
                for c in cards:
                    c["fetch_mode"] = fetch_mode
                    c["cached_at"] = cached_at
                    is_in_win = False
                    if c.get("starts_at"):
                        try:
                            dt_s = datetime.fromisoformat(c["starts_at"].replace("Z", "+00:00"))
                            loc_s = dt_s.astimezone(NY_TZ).date()
                            if win_start <= loc_s < win_end:
                                is_in_win = True
                        except Exception:
                            pass
                    store.upsert_event(c, method="official_cards", parent_url=parent_url or url, is_independent_new=is_in_win)
                    discovered_events += 1

                if depth <= 2 and html:
                    child_links = crawl_page_for_links(html, url)
                    for cl in child_links:
                        c_canon = canonicalize_url(cl)
                        if not store.get_frontier_item(c_canon):
                            store.enqueue_url(cl, discovery_parent=url, priority=item["priority"] - 1, depth=depth + 1)

                store.mark_frontier_status(url, "completed")
                continue

            # 1. Check if URL is a direct event
            is_event = False
            rec = None
            if "luma.com" in url or "lu.ma" in url:
                try:
                    rec = parse_luma_html(html, url=url, allow_non_food=True)
                    if rec:
                        is_event = True
                except Exception:
                    pass
            elif "eventbrite.com" in url and "/e/" in url:
                try:
                    rec = parse_eventbrite_html(html, url=url, allow_non_food=True)
                    if rec:
                        is_event = True
                except Exception:
                    pass

            if not is_event:
                try:
                    rec = parse_jsonld_event(html, url=url)
                    if rec:
                        is_event = True
                except Exception:
                    pass

            if is_event and rec:
                rec["fetch_mode"] = fetch_mode
                rec["cached_at"] = cached_at
                from_date_str = getattr(args, "from_date", None) or "2026-09-27"
                days_win = getattr(args, "days", None) or 7
                win_start = datetime.fromisoformat(from_date_str).date()
                win_end = win_start + timedelta(days=days_win)
                is_in_win = False
                if rec.get("starts_at"):
                    try:
                        dt_s = datetime.fromisoformat(rec["starts_at"].replace("Z", "+00:00"))
                        loc_s = dt_s.astimezone(NY_TZ).date()
                        if win_start <= loc_s < win_end:
                            is_in_win = True
                    except Exception:
                        pass

                store.upsert_event(rec, method="recursive_discovery", parent_url=parent_url, is_independent_new=is_in_win)
                discovered_events += 1

                # Expand host_url if present
                host_url = rec.get("host_url") or rec.get("parent_calendar_url")
                if host_url:
                    c_host = canonicalize_url(host_url)
                    if c_host and not store.get_frontier_item(c_host):
                        store.enqueue_url(host_url, discovery_parent=url, priority=item["priority"] + 1, depth=depth + 1)
                        print(f"    -> Enqueued host URL: {host_url}")

                # Also extract child links if depth <= 2
                if depth <= 2 and html:
                    child_links = crawl_page_for_links(html, url)
                    enq_count = 0
                    for cl in child_links:
                        c_canon = canonicalize_url(cl)
                        if not store.get_frontier_item(c_canon):
                            prio = 11 if ("eventbrite.com" in cl or "luma.com" in cl) else (item["priority"] - 1)
                            store.enqueue_url(cl, discovery_parent=url, priority=prio, depth=depth + 1)
                            enq_count += 1
                    if enq_count:
                        print(f"    -> Enqueued {enq_count} discovered child links (depth={depth+1})")

                store.mark_frontier_status(url, "completed")
                continue

            # 2. If it's a calendar or host page, extract child links (recursive 2-hop)
            if depth <= 2:
                child_links = crawl_page_for_links(html, url)
                enq_count = 0
                for cl in child_links:
                    c_canon = canonicalize_url(cl)
                    if not store.get_frontier_item(c_canon):
                        prio = 11 if ("eventbrite.com" in cl or "luma.com" in cl) else (item["priority"] - 1)
                        store.enqueue_url(cl, discovery_parent=url, priority=prio, depth=depth + 1)
                        enq_count += 1
                if enq_count:
                    print(f"    -> Enqueued {enq_count} discovered child links (depth={depth+1})")

            store.mark_frontier_status(url, "completed")

    store.sync_supplemental_json()
    print(f"[OK] Run complete: {requests_made} requests executed, {discovered_events} new events discovered.")


def main():
    parser = argparse.ArgumentParser(description="Yale Free Food Active Discovery CLI")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # plan subcommand
    plan_parser = subparsers.add_parser("plan", help="Generate discovery plan and search queries")
    plan_parser.add_argument("--from-date", default=date.today().isoformat(), help="Start date YYYY-MM-DD")
    plan_parser.add_argument("--days", type=int, default=7, help="Number of calendar days (default: 7)")
    plan_parser.add_argument("--profile", choices=["quick", "daily", "deep"], default="daily", help="Discovery profile")
    plan_parser.add_argument("--state-dir", default=None, help="State directory")
    plan_parser.add_argument("--json", action="store_true", help="Output JSON")

    # ingest-research subcommand
    ingest_parser = subparsers.add_parser("ingest-research", help="Ingest JSONL research results from Agent tools")
    ingest_parser.add_argument("--input", required=True, help="Path to research JSONL file")
    ingest_parser.add_argument("--state-dir", default=None, help="State directory")

    # run subcommand
    run_parser = subparsers.add_parser("run", help="Execute discovery crawler")
    run_parser.add_argument("--resume", action="store_true", help="Resume from frontier checkpoint")
    run_parser.add_argument("--profile", choices=["quick", "daily", "deep"], default="daily", help="Discovery profile")
    run_parser.add_argument("--from-date", default=date.today().isoformat(), help="Start date YYYY-MM-DD")
    run_parser.add_argument("--days", type=int, default=7, help="Number of days")
    run_parser.add_argument("--state-dir", default=None, help="State directory")

    args = parser.parse_args()

    if args.subcommand == "plan":
        execute_plan_command(args)
    elif args.subcommand == "ingest-research":
        execute_ingest_research_command(args)
    elif args.subcommand == "run":
        execute_run_command(args)


if __name__ == "__main__":
    main()
