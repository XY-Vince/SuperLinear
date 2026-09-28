#!/usr/bin/env python3
"""
Event Store and Runtime State Management for Yale Free Food Discovery Engine
=============================================================================
Manages persistent SQLite storage, frontier queues, multi-source deduplication,
field provenance, and backward-compatible JSON export/import.
"""

import re
import os
import sys
import json
import sqlite3
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qs, urlencode
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")


def canonicalize_url(url):
    """
    Standardize URL to prevent duplicate crawling and indexing.
    Strips tracking query parameters (utm_*, tk, ref, aff, etc.) and fragments.
    """
    if not url:
        return ""
    try:
        import html as html_lib
        url = html_lib.unescape(url.strip())
        u = urlsplit(url)
        # Eventbrite normalization: /e/... URLs are unique by path
        if "eventbrite.com" in (u.hostname or "").lower() and "/e/" in u.path:
            return f"{u.scheme.lower()}://{u.netloc.lower()}{u.path.rstrip('/')}"
        q = parse_qs(u.query)
        # YaleConnect normalization
        if (u.hostname or "").endswith(".yale.edu") and "id" in q:
            return f"yaleconnect:{q['id'][0]}"
        # Strip tracking params
        clean_q = {k: v for k, v in q.items() if not k.startswith("utm_") and k not in ("tk", "ref", "source", "aff", "keep_tld")}
        query_str = urlencode(clean_q, doseq=True) if clean_q else ""
        return urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path.rstrip("/"), query_str, ""))
    except Exception:
        return url.strip()


class EventStore:
    def __init__(self, state_dir=None):
        if state_dir:
            self.state_dir = Path(state_dir).expanduser().resolve()
        elif os.environ.get("YALE_FREE_FOOD_STORE"):
            p = Path(os.environ["YALE_FREE_FOOD_STORE"]).expanduser().resolve()
            self.state_dir = p if p.is_dir() else p.parent
        elif os.environ.get("YALE_FREE_FOOD_STATE_DIR"):
            self.state_dir = Path(os.environ["YALE_FREE_FOOD_STATE_DIR"]).expanduser().resolve()
        else:
            self.state_dir = (Path.home() / ".yale-free-food").resolve()

        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "state.db"
        self.json_store_path = self.state_dir / "supplemental_events.json"
        self.ledger_path = self.state_dir / "discovery-ledger.jsonl"
        self._init_db()

        # Ensure existing supplemental_events.json survives first write by importing if DB is empty
        if self.json_store_path.exists():
            with self._get_conn() as conn:
                count = conn.execute("SELECT count(*) FROM events").fetchone()[0]
                if count == 0:
                    self.import_legacy_json()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                canonical_url TEXT UNIQUE,
                url TEXT,
                title TEXT NOT NULL,
                starts_at TEXT,
                ends_at TEXT,
                dates TEXT,
                location TEXT,
                host TEXT,
                category TEXT,
                food_status TEXT,
                food_cost TEXT,
                admission_cost TEXT,
                confidence TEXT,
                record_kind TEXT,
                user_requested INTEGER DEFAULT 0,
                rsvp_status TEXT,
                eligibility TEXT,
                participation_note TEXT,
                discovery_method TEXT,
                discovery_parent TEXT,
                details_verified INTEGER DEFAULT 0,
                raw_json TEXT,
                first_seen_at TEXT,
                last_verified_at TEXT
            );

            CREATE TABLE IF NOT EXISTS sources (
                source_id TEXT PRIMARY KEY,
                entry_url TEXT,
                family TEXT,
                adapter TEXT,
                discovery_parent TEXT,
                status TEXT,
                date_capability TEXT,
                seasonality TEXT,
                last_success TEXT,
                next_check_at TEXT,
                discovered_count INTEGER DEFAULT 0,
                verified_count INTEGER DEFAULT 0,
                new_useful_count INTEGER DEFAULT 0,
                failure_reason TEXT
            );

            CREATE TABLE IF NOT EXISTS frontier (
                url TEXT PRIMARY KEY,
                canonical_url TEXT UNIQUE,
                discovery_parent TEXT,
                priority INTEGER DEFAULT 10,
                depth INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                scheduled_at TEXT,
                fetched_at TEXT,
                error_msg TEXT
            );

            CREATE TABLE IF NOT EXISTS discovery_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT,
                canonical_url TEXT,
                title TEXT,
                discovery_method TEXT,
                discovery_parent TEXT,
                discovered_at TEXT,
                verified_at TEXT,
                is_independent_new INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT,
                field_name TEXT,
                old_value TEXT,
                new_value TEXT,
                detected_at TEXT
            );
            """)

    def import_legacy_json(self, json_path=None):
        """Import legacy supplemental_events.json into SQLite if present."""
        target = Path(json_path).expanduser().resolve() if json_path else self.json_store_path
        if not target.exists():
            return 0
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            count = 0
            for ev in data.get("events", []) + data.get("unverified_food_leads", []):
                self.upsert_event(ev, method="legacy_json_import")
                count += 1
            return count
        except Exception as e:
            print(f"[WARN] Failed to import legacy JSON store {target}: {e}", file=sys.stderr)
            return 0

    def upsert_event(self, record, method=None, parent_url=None, is_independent_new=False):
        """
        Idempotent upsert with canonical URL deduplication and merge conflict preservation.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        eid = str(record.get("id") or "").strip()
        url = str(record.get("url") or "").strip()
        canon = canonicalize_url(url)
        if not canon:
            canon = None
        title_in = record.get("title")
        title = str(title_in).strip() if title_in else None
        starts_at = record.get("starts_at")
        ends_at = record.get("ends_at")
        dates = record.get("dates")
        location = record.get("location")
        host = record.get("host")
        category = record.get("category")
        food_status = record.get("food_status")
        food_cost = record.get("food_cost")
        admission_cost = record.get("admission_cost")
        confidence = record.get("confidence")
        record_kind = record.get("record_kind")
        user_requested = 1 if record.get("user_requested") else 0
        rsvp_status = record.get("rsvp_status")
        eligibility = record.get("eligibility")
        part_note = record.get("participation_note")
        disc_method = method or record.get("discovery_method") or "unknown"
        disc_parent = parent_url or record.get("discovery_parent") or ""
        details_verified = 1 if record.get("details_verified") else None
        raw_json = json.dumps(record, ensure_ascii=False)

        if not eid:
            seed = f"{canon or title or ''}|{starts_at or ''}"
            eid = f"disc:{hashlib.sha256(seed.encode()).hexdigest()[:12]}"

        with self._get_conn() as conn:
            # Check existing by id or canonical url
            cur = conn.cursor()
            existing = None
            if canon:
                cur.execute("SELECT * FROM events WHERE canonical_url = ? OR id = ?", (canon, eid))
                existing = cur.fetchone()
            else:
                cur.execute("SELECT * FROM events WHERE id = ?", (eid,))
                existing = cur.fetchone()

            # Cross-source dedup by matching starts_at and normalized title if not already matched
            if not existing and starts_at and title:
                norm_t = re.sub(r"[^a-zA-Z0-9]", "", title.lower())
                if len(norm_t) >= 6:
                    cur.execute("SELECT * FROM events WHERE starts_at = ?", (starts_at,))
                    candidates = cur.fetchall()
                    for c in candidates:
                        c_norm = re.sub(r"[^a-zA-Z0-9]", "", (c["title"] or "").lower())
                        if norm_t == c_norm or (norm_t in c_norm and len(norm_t) > 10) or (c_norm in norm_t and len(c_norm) > 10):
                            existing = c
                            break

            if existing:
                # Track field changes
                final_id = existing["id"]
                for f_name in ["starts_at", "location", "food_status", "food_cost", "admission_cost", "rsvp_status", "eligibility"]:
                    old_v = existing[f_name]
                    new_v = record.get(f_name)
                    if new_v is not None and str(old_v) != str(new_v) and str(old_v) != "None":
                        cur.execute(
                            "INSERT INTO changes (event_id, field_name, old_value, new_value, detected_at) VALUES (?, ?, ?, ?, ?)",
                            (final_id, f_name, str(old_v), str(new_v), now_iso)
                        )

                # Merge user_requested bit: never overwrite a 1 with 0
                merged_req = max(existing["user_requested"], user_requested)

                # Intelligently preserve verified fields rather than blindly overwriting
                if existing["food_status"] in ("provided", "likely") and (not food_status or food_status in ("unclear", "unknown")):
                    merged_food_status = existing["food_status"]
                else:
                    merged_food_status = food_status if food_status is not None else existing["food_status"]

                if existing["food_cost"] == "free" and (not food_cost or food_cost in ("unknown", "unclear")):
                    merged_food_cost = existing["food_cost"]
                else:
                    merged_food_cost = food_cost if food_cost is not None else existing["food_cost"]

                if existing["confidence"] == "confirmed_free" and (not confidence or confidence in ("needs_verification", "unknown")):
                    merged_confidence = existing["confidence"]
                else:
                    merged_confidence = confidence if confidence is not None else existing["confidence"]

                if existing["record_kind"] == "food_candidate" and merged_food_status in ("provided", "likely"):
                    merged_record_kind = "food_candidate"
                elif existing["record_kind"] == "user_interest":
                    merged_record_kind = "user_interest"
                else:
                    merged_record_kind = record_kind if record_kind is not None else existing["record_kind"]

                if existing["participation_note"] and (not part_note or len(str(existing["participation_note"])) > len(str(part_note))):
                    merged_part_note = existing["participation_note"]
                else:
                    merged_part_note = part_note or existing["participation_note"]

                cur.execute("""
                UPDATE events SET
                    title = COALESCE(?, title),
                    starts_at = COALESCE(?, starts_at),
                    ends_at = COALESCE(?, ends_at),
                    dates = COALESCE(?, dates),
                    location = COALESCE(?, location),
                    host = COALESCE(?, host),
                    category = COALESCE(?, category),
                    food_status = ?,
                    food_cost = ?,
                    admission_cost = COALESCE(?, admission_cost),
                    confidence = ?,
                    record_kind = ?,
                    user_requested = ?,
                    rsvp_status = COALESCE(?, rsvp_status),
                    eligibility = COALESCE(?, eligibility),
                    participation_note = ?,
                    details_verified = CASE WHEN ? IS NOT NULL THEN ? ELSE details_verified END,
                    last_verified_at = ?
                WHERE id = ?
                """, (
                    title, starts_at, ends_at, dates,
                    location, host, category,
                    merged_food_status, merged_food_cost, admission_cost,
                    merged_confidence, merged_record_kind, merged_req,
                    rsvp_status, eligibility, merged_part_note,
                    details_verified, details_verified,
                    now_iso, final_id
                ))
            else:
                final_id = eid
                cur.execute("""
                INSERT INTO events (
                    id, canonical_url, url, title, starts_at, ends_at, dates,
                    location, host, category, food_status, food_cost, admission_cost,
                    confidence, record_kind, user_requested, rsvp_status, eligibility,
                    participation_note, discovery_method, discovery_parent, details_verified,
                    raw_json, first_seen_at, last_verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    final_id, canon, url, title or "Untitled Event", starts_at, ends_at, dates or "",
                    location or "unknown", host or "unknown", category or "Campus/Social",
                    food_status or "unclear", food_cost or "unknown", admission_cost or "unknown",
                    confidence or "needs_verification", record_kind or ("food_candidate" if (starts_at and food_status in ("provided", "likely")) else "unverified_food_lead"),
                    user_requested, rsvp_status or "unknown", eligibility or "unknown",
                    part_note or "", disc_method, disc_parent, 1 if details_verified else 0,
                    raw_json, now_iso, now_iso
                ))

            # Record in discovery ledger
            actual_independent_new = 0
            if is_independent_new and not existing:
                cur.execute("SELECT 1 FROM discovery_ledger WHERE event_id = ? AND is_independent_new = 1", (final_id,))
                if not cur.fetchone():
                    actual_independent_new = 1

            cur.execute("""
            INSERT INTO discovery_ledger (
                event_id, canonical_url, title, discovery_method, discovery_parent,
                discovered_at, verified_at, is_independent_new
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                final_id, canon, title, disc_method, disc_parent,
                now_iso, now_iso, actual_independent_new
            ))

        self.export_to_json()
        return final_id

    def export_to_json(self):
        """Export current database to supplemental_events.json for tool compatibility."""
        events_list = []
        leads_list = []
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM events ORDER BY starts_at ASC")
            for r in cur.fetchall():
                ev = dict(r)
                ev["user_requested"] = bool(ev["user_requested"])
                ev["details_verified"] = bool(ev["details_verified"])
                if ev.get("raw_json"):
                    try:
                        raw = json.loads(ev["raw_json"])
                        # preserve enriched keys
                        for k in ("evidence", "evidence_text", "food_signals", "food_cost_notes", "admission_notes", "field_provenance", "fetch_mode", "cached_at", "interest_match"):
                            if k in raw and (k not in ev or ev[k] is None):
                                ev[k] = raw[k]
                    except Exception:
                        pass
                ev.pop("raw_json", None)

                if ev.get("record_kind") == "unverified_food_lead" or not ev.get("starts_at"):
                    leads_list.append(ev)
                else:
                    events_list.append(ev)

        data = {"events": events_list, "unverified_food_leads": leads_list}
        tmp = self.json_store_path.with_name(f".tmp_{self.json_store_path.name}")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(self.json_store_path)

    def export_ledger(self, out_path=None):
        target = Path(out_path).expanduser().resolve() if out_path else self.ledger_path
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM discovery_ledger ORDER BY id ASC")
            rows = [dict(r) for r in cur.fetchall()]
        with open(target, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return len(rows)

    def get_events_for_date(self, target_date_str):
        """Return all events active on target_date_str (YYYY-MM-DD in New York time)."""
        agenda = []
        unresolved = []
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM events")
            for r in cur.fetchall():
                ev = dict(r)
                ev["user_requested"] = bool(ev["user_requested"])
                ev["details_verified"] = bool(ev["details_verified"])
                if ev.get("raw_json"):
                    try:
                        raw = json.loads(ev["raw_json"])
                        for k in ("evidence", "evidence_text", "food_signals", "food_cost_notes", "admission_notes", "field_provenance", "fetch_mode", "cached_at", "interest_match"):
                            if k in raw and (k not in ev or ev[k] is None):
                                ev[k] = raw[k]
                    except Exception:
                        pass
                ev.pop("raw_json", None)
                starts_at = ev.get("starts_at")
                if not starts_at:
                    unresolved.append(ev)
                    continue
                try:
                    dt_start = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
                    loc_start = dt_start.astimezone(NY_TZ).date()
                    loc_end = loc_start
                    if ev.get("ends_at"):
                        try:
                            dt_end = datetime.fromisoformat(ev["ends_at"].replace("Z", "+00:00"))
                            loc_end = dt_end.astimezone(NY_TZ).date()
                        except Exception:
                            pass
                    target_d = datetime.fromisoformat(target_date_str).date()
                    if loc_start <= target_d <= loc_end:
                        agenda.append(ev)
                except Exception:
                    unresolved.append(ev)

        agenda.sort(key=lambda x: str(x.get("starts_at") or ""))
        return agenda, unresolved

    def get_events_for_range(self, from_date_str, days=7):
        """Return all events active in [from_date, from_date + days) half-open window."""
        start_d = datetime.fromisoformat(from_date_str).date()
        end_d = start_d + timedelta(days=days)
        agenda = []
        unresolved = []
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM events")
            for r in cur.fetchall():
                ev = dict(r)
                ev["user_requested"] = bool(ev["user_requested"])
                ev["details_verified"] = bool(ev["details_verified"])
                if ev.get("raw_json"):
                    try:
                        raw = json.loads(ev["raw_json"])
                        for k in ("evidence", "evidence_text", "food_signals", "food_cost_notes", "admission_notes", "field_provenance", "fetch_mode", "cached_at", "interest_match"):
                            if k in raw and (k not in ev or ev[k] is None):
                                ev[k] = raw[k]
                    except Exception:
                        pass
                ev.pop("raw_json", None)
                starts_at = ev.get("starts_at")
                if not starts_at:
                    unresolved.append(ev)
                    continue
                try:
                    dt_start = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
                    loc_start = dt_start.astimezone(NY_TZ).date()
                    loc_end = loc_start
                    if ev.get("ends_at"):
                        try:
                            dt_end = datetime.fromisoformat(ev["ends_at"].replace("Z", "+00:00"))
                            loc_end = dt_end.astimezone(NY_TZ).date()
                        except Exception:
                            pass
                    if loc_start < end_d and loc_end >= start_d:
                        agenda.append(ev)
                except Exception:
                    unresolved.append(ev)

        agenda.sort(key=lambda x: str(x.get("starts_at") or ""))
        return agenda, unresolved

    def add_frontier_url(self, url, parent_url="", priority=10, depth=0):
        canon = canonicalize_url(url)
        if not canon or canon.startswith("javascript:"):
            return False
        with self._get_conn() as conn:
            try:
                conn.execute("""
                INSERT INTO frontier (url, canonical_url, discovery_parent, priority, depth, status, scheduled_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
                ON CONFLICT(canonical_url) DO UPDATE SET
                    priority = MAX(priority, excluded.priority),
                    depth = MIN(depth, excluded.depth)
                """, (url, canon, parent_url, priority, depth, datetime.now(timezone.utc).isoformat()))
                return True
            except Exception:
                return False

    def get_pending_frontier(self, limit=20):
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
            SELECT * FROM frontier WHERE status = 'pending'
            ORDER BY priority DESC, depth ASC LIMIT ?
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]

    def mark_frontier_status(self, url, status="completed", error_msg=None):
        canon = canonicalize_url(url)
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
            UPDATE frontier SET status = ?, fetched_at = ?, error_msg = ? WHERE canonical_url = ?
            """, (status, now_iso, error_msg, canon))

    def enqueue_url(self, url, discovery_parent="", priority=10, depth=0):
        return self.add_frontier_url(url, parent_url=discovery_parent, priority=priority, depth=depth)

    def get_frontier_item(self, url):
        canon = canonicalize_url(url)
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM frontier WHERE canonical_url = ? OR url = ?", (canon, url))
            row = cur.fetchone()
            return dict(row) if row else None

    def sync_supplemental_json(self):
        return self.export_to_json()
