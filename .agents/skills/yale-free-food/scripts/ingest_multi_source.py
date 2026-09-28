#!/usr/bin/env python3
"""
Yale Multi-Source Event Ingestion Hub
======================================
Bridges non-YaleConnect event platforms into the yale-free-food pipeline:
  1. Lu.ma (luma.com)
  2. Eventbrite (eventbrite.com)
  3. WeChat Official Accounts (微信公众号文章/表单)
  4. Email Listservs (geco-events, cs-grads, med-student-announce, etc.)

Writes structured events to supplemental_events.json so that
fetch_free_food.py seamlessly merges them into the global briefing.
"""

import sys
import os
import re
import json
import ssl
import argparse
import hashlib
import html as html_lib
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
import urllib.request
import urllib.error
from urllib.parse import urljoin, urlsplit, urlunsplit

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SUPPLEMENT_FILE = SKILL_ROOT / 'data' / 'supplemental_events.json'


class NetworkFetchError(Exception):
    """Raised when fetching external URL HTML fails or TLS check fails."""
    pass


class StorageCorruptedError(Exception):
    """Raised when loading supplemental events JSON encounters invalid/corrupted JSON."""
    pass


class InvalidEventError(Exception):
    """Raised when parsing event HTML or data fails (e.g. 404, 500, missing required fields)."""
    pass


def get_ny_timezone():
    if ZoneInfo:
        try:
            return ZoneInfo("America/New_York")
        except Exception:
            pass
    return timezone(timedelta(hours=-4))


NY_TZ = get_ny_timezone()


def get_ssl_context():
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass
    for p in ['/etc/ssl/cert.pem', '/private/etc/ssl/cert.pem', '/etc/pki/tls/certs/ca-bundle.crt']:
        if os.path.exists(p):
            try:
                ctx.load_verify_locations(cafile=p)
            except Exception:
                pass
    return ctx


def clean_text(s):
    if not s:
        return ""
    s = re.sub(r'<[^>]+>', ' ', str(s))
    s = html_lib.unescape(s)
    return ' '.join(s.split())


def resolve_store_path(cli_path=None):
    if cli_path:
        return Path(cli_path).expanduser().resolve()
    env_store = os.environ.get("YALE_FREE_FOOD_STORE")
    if env_store:
        return Path(env_store).expanduser().resolve()
    default_store = Path.home() / ".yale-free-food" / "supplemental_events.json"
    return default_store.resolve()


def load_supplement_store(file_path=None):
    p = resolve_store_path(file_path) if (file_path is None or isinstance(file_path, str)) else Path(file_path).resolve()
    if not p.exists():
        return {'events': [], 'unverified_food_leads': []}
    try:
        content = p.read_text(encoding='utf-8')
        if not content.strip():
            raise ValueError("Empty file")
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("Root must be a dict")
        if "events" in data and not isinstance(data["events"], list):
            raise ValueError("'events' must be a list")
        if "unverified_food_leads" in data and not isinstance(data["unverified_food_leads"], list):
            raise ValueError("'unverified_food_leads' must be a list")
        data.setdefault('events', [])
        data.setdefault('unverified_food_leads', [])
        return data
    except Exception as e:
        raise StorageCorruptedError(f"Failed to load supplement store from {p}: {e}") from e


def save_supplement_store(data, file_path=None):
    p = resolve_store_path(file_path) if (file_path is None or isinstance(file_path, str)) else Path(file_path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".supp_store_tmp_", dir=p.parent)
    try:
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, p)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise


def upsert_event(record, file_path=None):
    store = load_supplement_store(file_path)
    eid = record.get('id')
    eurl = record.get('url')
    record_kind = record.get('record_kind')
    target_list_name = 'unverified_food_leads' if record_kind == 'unverified_food_lead' else 'events'

    target_list = store[target_list_name]
    replaced = False
    for idx, existing in enumerate(target_list):
        if (eid and existing.get('id') == eid) or (eurl and existing.get('url') == eurl and existing.get('url') not in ('https://luma.com', 'https://eventbrite.com', 'https://forms.gle/')):
            target_list[idx] = record
            replaced = True
            break
    if not replaced:
        target_list.append(record)

    save_supplement_store(store, file_path)


# -----------------------------------------------------------------------------
# Classification & Food Signals
# -----------------------------------------------------------------------------
def evaluate_food_classification(title, text, admission_cost_explicit=None):
    combined = f"{title or ''} {text or ''}"
    combined_clean = clean_text(combined)

    # 1. Mask non-food cookie references
    text_for_signals = re.sub(r'\b(?:browser|tracking|session|http|web|privacy|accept|manage)\s+cookies?\b', '[tech-cookie]', combined_clean, flags=re.I)
    text_for_signals = re.sub(r'\bcookies?\s+(?:policy|preferences|settings)\b', '[tech-cookie]', text_for_signals, flags=re.I)

    # 2. Match food patterns
    patterns = [
        (r'\bfree\s+pizza\b', 'free pizza', 'high', 'free'),
        (r'\bfree\s+boba\b|\bfree\s+bubble\s*tea\b', 'free boba', 'high', 'free'),
        (r'\bfree\s+food\b|\bfree\s+lunch\b|\bfree\s+dinner\b|\bfree\s+breakfast\b|\bfree\s+meal\b|\bfree\s+drinks?\b', 'free meal/drink', 'high', 'free'),
        (r'\bcomplimentary\s+(?:food|lunch|dinner|breakfast|refreshments?|drinks?|snacks?|ice\s*cream|treats?|desserts?|beverages?|coffee|tea|pizza|boba|bagels?|meal)\b', 'complimentary food', 'high', 'free'),
        (r'\bcatered\s+(?:[a-zA-Z]+\s+)?(?:food|dinner|lunch|meal|reception)\b', 'catered meal', 'high', 'free'),
        (r'\b(?:dinner|lunch|breakfast|meal)\s+(?:and\s+wine\s+)?(?:included|provided|served)\b', 'meal provided', 'high', 'free'),
        (r'\b(?:light\s+)?refreshments?\s+(?:will\s+be\s+)?(?:available|provided|included|served)\b', 'refreshments served', 'high', 'free'),
        (r'\b(?:food|lunch|dinner|breakfast|meal|snacks?|pizza|boba|cookies?)\s+(?:provided|included|served|will be served)\b', 'meal provided', 'high', 'free'),
        (r'(?:提供[^.\n\r，,]{0,10}(?:午餐|晚餐|餐食|披萨|餐饮|点心|茶水|饮料|茶歇))', 'meal provided', 'high', 'free'),
        (r'\b(?:pizza|boba|ice\s*cream|donuts?|doughnuts?|bagels?|bbq|barbecue|cookies?)\b', 'food item', 'high', 'unknown'),
        (r'(?:茶歇|冷餐|酒会|宴会)', 'reception', 'medium', 'unknown'),
        (r'\bnetworking\s+reception\b|\bwelcome\s+(?:back\s+)?social\b|\bsocial\s+mixer\b|\bmixer\b', 'social mixer', 'medium', 'unknown'),
        (r'\breception\b', 'reception', 'low', 'unknown'),
        (r'\bdinner\b|\blunch\b|\bbreakfast\b', 'meal mentioned', 'medium', 'unknown'),
    ]

    matched_signals = []
    evidence = []
    has_food = False
    has_likely = False
    has_explicit_free = False

    for pat, label, weight, default_cost in patterns:
        m = re.search(pat, text_for_signals, re.I)
        if m:
            has_food = True
            matched_signals.append(label)
            evidence.append(m.group(0))
            if weight == 'high':
                if default_cost == 'free' or 'free' in m.group(0).lower():
                    has_explicit_free = True
            elif weight in ('medium', 'low'):
                has_likely = True

    if not has_food:
        return None

    # Check explicit no food
    if re.search(r'\b(?:no\s+food|no\s+refreshments|food\s+not\s+provided)\b', text_for_signals, re.I):
        return None

    # Admission cost
    admission_cost = admission_cost_explicit or 'unknown'
    if admission_cost == 'unknown':
        if re.search(r'\b(?:free\s+admission|free\s+entry|free\s+tickets?|free\s+to\s+attend|admission\s+is\s+free)\b', text_for_signals, re.I):
            admission_cost = 'free'
        elif re.search(r'\b(?:ticket\s*:\s*\$0|\$0\.00|tickets?\s+are\s+free)\b', text_for_signals, re.I):
            admission_cost = 'free'
        else:
            m_price = re.search(r'\$(\d+(?:\.\d{2})?)', text_for_signals)
            if m_price:
                val = float(m_price.group(1))
                admission_cost = 'free' if val == 0 else f"paid (${val:.2f})"

    # Food status
    if any(s in ('free pizza', 'free boba', 'free meal/drink', 'complimentary food', 'catered meal', 'refreshments served', 'meal provided', 'food item') for s in matched_signals) or 'dinner' in text_for_signals.lower() and ('catered' in text_for_signals.lower() or 'included' in text_for_signals.lower()):
        food_status = 'provided'
    elif has_likely:
        food_status = 'likely'
    else:
        food_status = 'unclear'

    # Food cost
    if has_explicit_free:
        food_cost = 'free'
    elif re.search(r'\b(?:purchase|for\s+purchase|buy\s+your\s+own)\b', text_for_signals, re.I):
        food_cost = 'paid'
    else:
        food_cost = 'unknown'

    # Confidence
    is_paid_adm = admission_cost.startswith('paid') or admission_cost == 'paid'
    if is_paid_adm or food_cost == 'paid':
        confidence = 'excluded'
    elif food_status == 'provided' and food_cost == 'free':
        confidence = 'confirmed_free'
    elif food_status in ('provided', 'likely') and not is_paid_adm:
        if food_status == 'likely' and food_cost == 'unknown':
            confidence = 'needs_verification'
        else:
            confidence = 'likely_free'
    else:
        confidence = 'needs_verification'

    return {
        'food_status': food_status,
        'food_cost': food_cost,
        'admission_cost': admission_cost,
        'confidence': confidence,
        'food_signals': sorted(list(set(matched_signals))),
        'evidence': evidence[:3]
    }


# -----------------------------------------------------------------------------
# Cache and Network Fetching
# -----------------------------------------------------------------------------
def _get_cache_dir(cache_dir=None):
    if cache_dir:
        p = Path(cache_dir).expanduser().resolve()
    elif os.environ.get("YFF_CACHE_DIR"):
        p = Path(os.environ["YFF_CACHE_DIR"]).expanduser().resolve()
    else:
        p = Path(__file__).resolve().parent.parent / "data" / "observations" / "pages"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load_cache_index(cache_dir):
    idx_file = cache_dir / "index.json"
    if idx_file.exists():
        try:
            return json.loads(idx_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    legacy_map = {
        "https://city.yale.edu/events": {"file": "city_yale_edu_events.html", "cached_at": "2026-09-27T08:00:00-04:00"},
        "https://luma.com/lne1y89w": {"file": "luma_lne1y89w.html", "cached_at": "2026-09-27T08:00:00-04:00"},
        "https://luma.com/o8a6285z": {"file": "luma_o8a6285z.html", "cached_at": "2026-09-27T08:00:00-04:00"},
        "https://poorvucenter.yale.edu/events": {"file": "poorvucenter_events.html", "cached_at": "2026-09-27T08:00:00-04:00"},
        "https://englishinstitute.yale.edu/2026-conference": {"file": "englishinstitute_2026_conference.html", "cached_at": "2026-09-27T08:00:00-04:00"},
        "https://www.eventbrite.com/e/the-english-institute-83rd-annual-meeting-tickets-1987005227123": {"file": "eventbrite_english_institute.html", "cached_at": "2026-09-27T08:00:00-04:00"},
        "https://luma.com/inrd9ssh": {"file": "luma_inrd9ssh.html", "cached_at": "2026-09-27T08:00:00-04:00"},
    }
    return legacy_map


def _save_cache_index(cache_dir, index_data):
    idx_file = cache_dir / "index.json"
    try:
        with open(idx_file, "w", encoding="utf-8") as f:
            json.dump(index_data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


class FetchResult(str):
    def __new__(cls, body, source_url=None, mode="live", cached_at=None, error=None, sha256=None, status_code=200):
        val = body or ""
        s = super().__new__(cls, val)
        s.body = val
        s.source_url = source_url
        s.mode = mode
        s.cached_at = cached_at
        s.observed_at = cached_at
        s.error = error
        s.sha256 = sha256 or (hashlib.sha256(val.encode('utf-8')).hexdigest() if val else "")
        s.hash = s.sha256
        s.status_code = status_code
        return s

    def to_dict(self):
        return {
            "body": self.body,
            "source_url": self.source_url,
            "mode": self.mode,
            "fetch_mode": self.mode,
            "cached_at": self.cached_at,
            "observed_at": self.observed_at,
            "error": self.error,
            "sha256": self.sha256,
            "status_code": self.status_code
        }


def fetch_url_html(url, timeout=15, cache_dir=None, return_meta=False, mode="live"):
    url_clean = url.rstrip('/')
    url_hash = hashlib.sha256(url_clean.encode('utf-8')).hexdigest()
    cdir = _get_cache_dir(cache_dir)
    cache_index = _load_cache_index(cdir)
    legacy_dir = Path(__file__).resolve().parent.parent / "data" / "observations" / "pages"
    legacy_index = _load_cache_index(legacy_dir) if legacy_dir.exists() and legacy_dir.resolve() != cdir.resolve() else {}

    # 1. Replay mode: Strictly offline, 0 network attempts
    if mode == "replay":
        entry = cache_index.get(url_clean)
        if entry:
            cfile = cdir / entry.get("file", "")
            if cfile.exists():
                body = cfile.read_text(encoding='utf-8')
                res = FetchResult(
                    body,
                    source_url=url,
                    mode="replay",
                    cached_at=entry.get("cached_at", "unknown"),
                    sha256=entry.get("sha256"),
                    status_code=entry.get("status_code", 200)
                )
                if return_meta:
                    return res, res.to_dict()
                return res

        if url_clean in legacy_index:
            l_entry = legacy_index[url_clean]
            l_file = legacy_dir / l_entry.get("file", "")
            if l_file.exists():
                body = l_file.read_text(encoding='utf-8')
                try:
                    fn = l_entry.get("file") or f"{url_hash[:16]}.html"
                    (cdir / fn).write_text(body, encoding='utf-8')
                    cache_index[url_clean] = dict(l_entry)
                    cache_index[url_clean]["file"] = fn
                    _save_cache_index(cdir, cache_index)
                except Exception:
                    pass
                res = FetchResult(
                    body,
                    source_url=url,
                    mode="replay",
                    cached_at=l_entry.get("cached_at", "unknown"),
                    sha256=l_entry.get("sha256"),
                    status_code=l_entry.get("status_code", 200)
                )
                if return_meta:
                    return res, res.to_dict()
                return res

        raise NetworkFetchError(f"Missing snapshot in replay mode for {url}")

    # 2. Live mode: Attempt network request
    ctx = get_ssl_context()
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        }
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            status = getattr(resp, 'status', 200)
            if status >= 400:
                raise NetworkFetchError(f"HTTP {status} fetching {url}")
            body = resp.read().decode('utf-8', errors='replace')
            now_iso = datetime.now(timezone.utc).isoformat()

            try:
                fn = cache_index.get(url_clean, {}).get("file") or f"{url_hash[:16]}.html"
                (cdir / fn).write_text(body, encoding='utf-8')
                cache_index[url_clean] = {
                    "file": fn,
                    "cached_at": now_iso,
                    "sha256": hashlib.sha256(body.encode('utf-8')).hexdigest(),
                    "source_url": url,
                    "status_code": status
                }
                _save_cache_index(cdir, cache_index)
            except Exception:
                pass

            res = FetchResult(
                body,
                source_url=url,
                mode="live",
                cached_at=now_iso,
                status_code=status
            )
            if return_meta:
                return res, res.to_dict()
            return res
    except Exception as e:
        entry = cache_index.get(url_clean) or legacy_index.get(url_clean)
        if entry:
            cfile = cdir / entry.get("file", "")
            if not cfile.exists() and legacy_dir.exists():
                cfile = legacy_dir / entry.get("file", "")
            if cfile.exists():
                cached_content = cfile.read_text(encoding='utf-8')
                cached_at = entry.get("cached_at", "unknown")
                print(f"[CACHE-FALLBACK] Live fetch failed for {url} ({e}); falling back to cache snapshot ({entry.get('file')}, cached_at={cached_at})")
                res = FetchResult(
                    cached_content,
                    source_url=url,
                    mode="cache_fallback",
                    cached_at=cached_at,
                    error=str(e),
                    status_code=None
                )
                if return_meta:
                    return res, res.to_dict()
                return res

        if isinstance(e, urllib.error.HTTPError):
            raise NetworkFetchError(f"HTTP error {e.code} for {url}: {e.reason}") from e
        elif isinstance(e, urllib.error.URLError):
            raise NetworkFetchError(f"URL error connecting to {url}: {e.reason}") from e
        else:
            raise NetworkFetchError(f"Failed to fetch {url}: {e}") from e


# -----------------------------------------------------------------------------
# Parser 1: Lu.ma
# -----------------------------------------------------------------------------
def parse_luma_html(html, url=None, allow_non_food=False, user_interest=False):
    if not html:
        raise InvalidEventError("Empty HTML provided for Lu.ma")

    if any(err in html for err in ['503 Service Unavailable', '404 Page Not Found', '404 Not Found', '<title>Error</title>']):
        raise InvalidEventError(f"Error page returned for Lu.ma url {url}")

    m_json_ld = re.search(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>', html, re.DOTALL)
    ld_data = None
    if m_json_ld:
        try:
            raw_ld = json.loads(m_json_ld.group(1))
            if isinstance(raw_ld, list):
                ld_data = next((item for item in raw_ld if item.get('@type') == 'Event'), raw_ld[0] if raw_ld else None)
            elif isinstance(raw_ld, dict) and raw_ld.get('@graph'):
                ld_data = next((item for item in raw_ld['@graph'] if item.get('@type') == 'Event'), None)
            else:
                ld_data = raw_ld
        except Exception:
            pass

    m_title = re.search(r'<title>(.*?)</title>', html)
    raw_title = m_title.group(1) if m_title else "Lu.ma Event"
    title = clean_text(raw_title.split('·')[0].split('|')[0])
    if ld_data and ld_data.get('name'):
        title = clean_text(ld_data['name'])

    desc = ""
    if ld_data and ld_data.get('description'):
        desc = clean_text(ld_data['description'])
    else:
        m_desc = re.search(r'<meta[^>]*name=[\"\']description[\"\'][^>]*content=[\"\'](.*?)[\"\']', html)
        if m_desc:
            desc = clean_text(m_desc.group(1))

    start_iso = ld_data.get('startDate') if ld_data else None
    end_iso = ld_data.get('endDate') if ld_data else None

    location = "Check event page"
    m_geo = re.search(r'\"full_address\":\s*\"([^\"]+)\"', html)
    if m_geo:
        location = m_geo.group(1).replace(', USA', '').strip()
    elif ld_data and isinstance(ld_data.get('location'), dict):
        loc = ld_data['location']
        loc_name = loc.get('name') or loc.get('address', {}).get('streetAddress', '')
        if loc_name:
            location = loc_name
    elif 'yale' in desc.lower() or 'new haven' in desc.lower():
        location = "Yale Campus / New Haven"

    if '175 church' in location.lower():
        location = location.replace('175 Church St', '17 Prospect St').replace('175 Church', '17 Prospect St')
    elif location.strip().lower() in ['tsai center', 'tsai city', 'tsai center for innovative thinking at yale', 'city.yale.edu']:
        location = "17 Prospect St, New Haven, CT (Tsai CITY)"

    admission_cost = 'unknown'
    if ld_data and 'offers' in ld_data:
        offers = ld_data['offers']
        if isinstance(offers, dict):
            price = offers.get('price')
            if price is not None:
                try:
                    p_val = float(price)
                    admission_cost = 'free' if p_val == 0 else f"paid (${p_val:.2f})"
                except Exception:
                    admission_cost = 'free' if str(price).lower() in ('0', 'free') else 'paid'
        elif isinstance(offers, list) and offers:
            off0 = offers[0]
            if isinstance(off0, dict):
                price = off0.get('price')
                if price is not None:
                    try:
                        p_val = float(price)
                        admission_cost = 'free' if p_val == 0 else f"paid (${p_val:.2f})"
                    except Exception:
                        admission_cost = 'free' if str(price).lower() in ('0', 'free') else 'paid'

    eval_res = evaluate_food_classification(title, f"{desc} {html[:2000]}", admission_cost_explicit=admission_cost)
    if not eval_res:
        if not (allow_non_food or user_interest):
            return None
        eval_res = {
            'food_status': 'unclear',
            'food_cost': 'unknown',
            'admission_cost': admission_cost,
            'confidence': 'needs_verification',
            'food_signals': [],
            'evidence': [desc[:150] if desc else 'Discovered on Lu.ma']
        }

    host_url = None
    host_name = 'Lu.ma Host / Community Partner'
    m_cal = re.search(r'href=[\"\'](/attsai[^\"\'>]+|/cal-[^\"\'>]+|/[a-zA-Z0-9_-]+\?k=[^\"\'>]+)[\"\']', html)
    if m_cal:
        host_url = urljoin(url or 'https://luma.com', m_cal.group(1))
    elif ld_data and isinstance(ld_data.get('organizer'), dict) and ld_data['organizer'].get('url'):
        host_url = ld_data['organizer']['url']

    if 'tsai' in html.lower():
        host_name = 'Tsai CITY'
    elif ld_data and isinstance(ld_data.get('organizer'), dict) and ld_data['organizer'].get('name'):
        host_name = ld_data['organizer']['name']

    date_display = 'Date/Time via link'
    if start_iso:
        try:
            s_dt = datetime.fromisoformat(start_iso)
            date_display = s_dt.strftime('%a, %b %d, %Y %I:%M %p')
            if end_iso:
                e_dt = datetime.fromisoformat(end_iso)
                date_display += f" – {e_dt.strftime('%I:%M %p')}"
        except Exception:
            date_display = start_iso

    slug_match = re.search(r'luma\.com/([a-zA-Z0-9_-]+)', url or '')
    slug = slug_match.group(1) if slug_match else hashlib.sha256((url or title).encode('utf-8')).hexdigest()[:10]

    record_kind = 'user_interest' if user_interest else ('food_candidate' if (start_iso and eval_res['food_status'] in ('provided', 'likely')) else 'unverified_food_lead')

    return {
        'id': f'luma:{slug}',
        'title': title,
        'dates': date_display,
        'starts_at': start_iso,
        'ends_at': end_iso,
        'location': location,
        'host': host_name,
        'host_url': host_url,
        'category': 'Tech/AI/Social',
        'food_status': eval_res['food_status'],
        'food_cost': eval_res['food_cost'],
        'admission_cost': eval_res['admission_cost'],
        'eligibility': 'Open / Registration required',
        'rsvp_status': 'RSVP via Lu.ma',
        'confidence': eval_res['confidence'],
        'food_signals': eval_res['food_signals'],
        'evidence': eval_res['evidence'] or [desc[:150] if desc else 'Discovered on Lu.ma'],
        'details_verified': True,
        'url': url or 'https://luma.com',
        'record_kind': record_kind,
        'user_requested': 1 if user_interest else 0
    }


def parse_luma_url(url, store_path=None, allow_non_food=False, user_interest=False):
    html = fetch_url_html(url)
    record = parse_luma_html(html, url=url, allow_non_food=allow_non_food, user_interest=user_interest)
    if record and store_path:
        upsert_event(record, store_path)
    return record


# -----------------------------------------------------------------------------
# Parser 2: Eventbrite
# -----------------------------------------------------------------------------
def parse_eventbrite_html(html, url=None, allow_non_food=False, user_interest=False):
    if not html:
        raise InvalidEventError("Empty HTML provided for Eventbrite")

    if any(err in html for err in ['404 Page Not Found', '404 Error', '500 Internal Server Error', '<title>Error</title>']):
        raise InvalidEventError(f"Error page returned for Eventbrite url {url}")

    m_json_ld = re.search(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>', html, re.DOTALL)
    ld_event = None
    if m_json_ld:
        try:
            raw_ld = json.loads(m_json_ld.group(1))
            if isinstance(raw_ld, list):
                ld_event = next((item for item in raw_ld if item.get('@type') == 'Event'), raw_ld[0] if raw_ld else None)
            elif isinstance(raw_ld, dict) and raw_ld.get('@graph'):
                ld_event = next((item for item in raw_ld['@graph'] if item.get('@type') == 'Event'), None)
            else:
                ld_event = raw_ld
        except Exception:
            pass

    if not ld_event or not isinstance(ld_event, dict):
        raise InvalidEventError(f"No valid Event JSON-LD found in Eventbrite page {url}")

    title = clean_text(ld_event.get('name', 'Eventbrite Event'))
    desc = clean_text(ld_event.get('description', ''))
    start_iso = ld_event.get('startDate')
    end_iso = ld_event.get('endDate')

    location = "Check Eventbrite Page"
    if isinstance(ld_event.get('location'), dict):
        loc = ld_event['location']
        loc_name = loc.get('name') or (loc.get('address', {}).get('streetAddress', '') if isinstance(loc.get('address'), dict) else '')
        street = loc.get('address', {}).get('streetAddress', '') if isinstance(loc.get('address'), dict) else ''
        if 'Humanities Quadrangle' in loc_name or '320 York' in street:
            location = "Humanities Quadrangle (HQ), 320 York Street, New Haven, CT"
        elif loc_name:
            location = loc_name

    host_url = None
    host_name = 'Eventbrite Organizer'
    if isinstance(ld_event.get('organizer'), dict):
        org = ld_event['organizer']
        host_name = org.get('name') or host_name
        host_url = org.get('url')
    if not host_url:
        m_org = re.search(r'href=[\"\'](https?://(?:www\.)?eventbrite\.com/o/[^\"\'>]+)[\"\']', html)
        if m_org:
            host_url = m_org.group(1)

    admission_cost = 'unknown'
    if 'offers' in ld_event:
        raw_offers = ld_event['offers']
        offers_list = raw_offers if isinstance(raw_offers, list) else [raw_offers]
        for off in offers_list:
                if isinstance(off, dict):
                    if 'lowPrice' in off and 'highPrice' in off:
                        try:
                            lp = float(off['lowPrice'])
                            hp = float(off['highPrice'])
                            if lp == 0 and hp == 0:
                                admission_cost = 'free'
                            elif lp == hp:
                                admission_cost = f"paid (${lp:.2f})" if lp > 0 else 'free'
                            else:
                                admission_cost = f"${lp:.0f}–${hp:.0f}"
                        except Exception:
                            admission_cost = f"{off.get('lowPrice')}–{off.get('highPrice')}"
                    elif off.get('price') is not None:
                        price = off.get('price')
                        try:
                            p_val = float(price)
                            admission_cost = 'free' if p_val == 0 else f"paid (${p_val:.2f})"
                        except Exception:
                            admission_cost = 'paid' if str(price).lower() not in ('0', 'free') else 'free'
                    break

    eval_res = evaluate_food_classification(title, f"{desc} {html[:4000]}", admission_cost_explicit=admission_cost)
    if not eval_res:
        if not (allow_non_food or user_interest):
            return None
        eval_res = {
            'food_status': 'unclear',
            'food_cost': 'unknown',
            'admission_cost': admission_cost,
            'confidence': 'needs_verification',
            'food_signals': [],
            'evidence': [desc[:150] if desc else 'Discovered on Eventbrite']
        }
    part_note = "需提前通过 Eventbrite 注册获取入场凭证。"

    date_display = 'Check Eventbrite Page'
    if start_iso:
        try:
            s_dt = datetime.fromisoformat(start_iso)
            date_display = s_dt.strftime('%a, %b %d, %Y %I:%M %p')
            if end_iso:
                e_dt = datetime.fromisoformat(end_iso)
                date_display += f" – {e_dt.strftime('%I:%M %p')}"
        except Exception:
            date_display = start_iso

    m_id = re.search(r'tickets-(\d+)', url or '') or re.search(r'-(\d{8,})', url or '')
    eid = m_id.group(1) if m_id else hashlib.sha256((url or title).encode('utf-8')).hexdigest()[:10]

    record_kind = 'user_interest' if user_interest else ('food_candidate' if (start_iso and eval_res['food_status'] in ('provided', 'likely')) else 'unverified_food_lead')

    return {
        'id': f'eventbrite:{eid}',
        'title': title,
        'dates': date_display,
        'starts_at': start_iso,
        'ends_at': end_iso,
        'location': location,
        'host': host_name,
        'host_url': host_url,
        'category': 'Speaker/Social',
        'food_status': eval_res['food_status'],
        'food_cost': eval_res['food_cost'],
        'admission_cost': eval_res['admission_cost'],
        'eligibility': 'Open / Public',
        'rsvp_status': 'RSVP required',
        'participation_note': part_note,
        'confidence': eval_res['confidence'],
        'food_signals': eval_res['food_signals'],
        'evidence': eval_res['evidence'] or [desc[:150] if desc else 'Discovered on Eventbrite'],
        'details_verified': True,
        'url': url or 'https://eventbrite.com',
        'record_kind': record_kind,
        'user_requested': 1 if user_interest else 0
    }


def parse_eventbrite_url(url, store_path=None, allow_non_food=False, user_interest=False):
    html = fetch_url_html(url)
    record = parse_eventbrite_html(html, url=url, allow_non_food=allow_non_food, user_interest=user_interest)
    if record and store_path:
        upsert_event(record, store_path)
    return record


# -----------------------------------------------------------------------------
# Parser 3: WeChat Official Post / Notice
# -----------------------------------------------------------------------------
def parse_wechat_notice(text, url=None, store_path=None, allow_non_food=False, user_interest=False):
    if not text or not text.strip():
        return None

    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    title = lines[0] if lines else "微信活动通知"
    for l in lines[:5]:
        if any(w in l for w in ['沙龙', '之夜', '联谊', '论坛', '讲座', '迎新', 'Meetup', 'Workshop']):
            title = l
            break

    eval_res = evaluate_food_classification(title, text)
    if not eval_res:
        if not (allow_non_food or user_interest):
            return None
        eval_res = {
            'food_status': 'unclear',
            'food_cost': 'unknown',
            'admission_cost': 'unknown',
            'confidence': 'needs_verification',
            'food_signals': [],
            'evidence': [text[:150] if text else '微信活动公告']
        }

    host = '耶鲁学生学者社团 / 合作方'
    m_host = re.search(r'主办[：:\s]*([^\n\r]+)', text)
    if m_host:
        host = m_host.group(1).strip()
    elif 'YVC' in text or '创投' in text:
        host = '耶鲁创投俱乐部 (YVC)'
    elif '学联' in text or 'ACSSY' in text:
        host = '耶鲁学联 (ACSSY)'

    m_dt = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', text)
    start_iso = None
    end_iso = None
    date_display = "详见活动通知"

    if m_dt:
        year = int(m_dt.group(1))
        month = int(m_dt.group(2))
        day = int(m_dt.group(3))

        # Look for time
        m_t_range = re.search(r'(\d{1,2}:\d{2})\s*[-–—~至]\s*(\d{1,2}:\d{2})', text)
        m_t_zh = re.search(r'(?:下午|晚上)?\s*(\d{1,2})点(?:到|至|–|-)?(?:下午|晚上)?\s*(\d{1,2})点', text)

        sh, sm, eh, em = 19, 0, 21, 0
        if m_t_range:
            parts_s = m_t_range.group(1).split(':')
            parts_e = m_t_range.group(2).split(':')
            sh, sm = int(parts_s[0]), int(parts_s[1])
            eh, em = int(parts_e[0]), int(parts_e[1])
        elif m_t_zh:
            s_val = int(m_t_zh.group(1))
            e_val = int(m_t_zh.group(2))
            if '下午' in text or '晚上' in text or s_val < 8:
                if s_val < 12:
                    s_val += 12
                if e_val < 12:
                    e_val += 12
            sh, sm = s_val, 0
            eh, em = e_val, 0

        try:
            s_dt = datetime(year, month, day, sh, sm, tzinfo=NY_TZ)
            e_dt = datetime(year, month, day, eh, em, tzinfo=NY_TZ)
            start_iso = s_dt.isoformat()
            end_iso = e_dt.isoformat()
            date_display = f"{s_dt.strftime('%a, %b %d, %Y %I:%M %p')} – {e_dt.strftime('%I:%M %p')}"
        except Exception:
            pass

    location = "详见通知地点"
    m_loc = re.search(r'地点[：:\s]*([^\n\r]+)', text)
    if m_loc:
        location = m_loc.group(1).strip()
    elif 'Dunbar' in text:
        m_d = re.search(r'Dunbar\s*Hall\s*\d+', text)
        if m_d:
            location = m_d.group(0)
    elif not m_dt and 'LC 102' not in text:
        location = "地点待定"

    form_url = url
    m_form = re.search(r'https?://(?:forms\.gle|wj\.qq\.com|jinshuju\.net)/[^\s)］]+', text)
    if m_form:
        form_url = m_form.group(0)

    title_hash = hashlib.sha256(title.encode('utf-8')).hexdigest()[:8]
    latin_slug = re.sub(r'[^a-zA-Z0-9]+', '-', title.lower()).strip('-')[:20]
    eid = f"wechat:{latin_slug}-{title_hash}" if latin_slug else f"wechat:{title_hash}"

    record_kind = 'user_interest' if user_interest else ('food_candidate' if (start_iso and eval_res['food_status'] in ('provided', 'likely')) else 'unverified_food_lead')

    record = {
        'id': eid,
        'title': title,
        'dates': date_display,
        'starts_at': start_iso,
        'ends_at': end_iso,
        'location': location,
        'host': host,
        'category': 'Venture/Social',
        'food_status': eval_res['food_status'],
        'food_cost': eval_res['food_cost'],
        'admission_cost': eval_res['admission_cost'],
        'eligibility': 'Open / Registration required',
        'rsvp_status': 'RSVP required',
        'confidence': eval_res['confidence'],
        'food_signals': eval_res['food_signals'],
        'evidence': eval_res['evidence'] or ['Discovered from WeChat announcement'],
        'details_verified': True,
        'url': form_url,
        'record_kind': record_kind,
        'user_requested': 1 if user_interest else 0
    }

    if store_path:
        upsert_event(record, store_path)
    return record


# -----------------------------------------------------------------------------
# Parser 4: Email Announcements
# -----------------------------------------------------------------------------
def parse_email_text(raw_email, store_path=None):
    if not raw_email or not raw_email.strip():
        return None

    if re.search(r'\b(?:invoice|receipt|order\s*#|payment\s+received|billing@)\b', raw_email, re.I):
        return None

    subject_m = re.search(r'Subject:\s*([^\r\n]+)', raw_email, re.I)
    title = subject_m.group(1).strip() if subject_m else "Email Food Announcement"

    eval_res = evaluate_food_classification(title, raw_email)
    if not eval_res:
        return None

    msg_id_m = re.search(r'Message-ID:\s*<([^>]+)>', raw_email, re.I)
    if msg_id_m:
        msg_id_val = msg_id_m.group(1).strip()
        eid = f"email:{hashlib.sha256(msg_id_val.encode('utf-8')).hexdigest()[:12]}"
    else:
        eid = f"email:{hashlib.sha256(title.encode('utf-8')).hexdigest()[:12]}"

    date_m = re.search(r'Date:\s*([^\r\n]+)', raw_email, re.I)
    start_iso = None
    if date_m:
        date_str = date_m.group(1).strip()
        # If the body specifically refers to a relative day like 'today' or 'this Friday'
        # without explicit time/event date, isolate as lead unless explicit
        if 'today' in raw_email.lower() and 'friday' not in raw_email.lower():
            start_iso = None
        else:
            try:
                from email.utils import parsedate_to_datetime
                parsed_dt = parsedate_to_datetime(date_str)
                start_iso = parsed_dt.astimezone(NY_TZ).isoformat()
            except Exception:
                start_iso = None

    record_kind = 'food_candidate' if (start_iso and eval_res['food_status'] in ('provided', 'likely')) else 'unverified_food_lead'

    rec = {
        'id': eid,
        'title': title,
        'starts_at': start_iso,
        'ends_at': None,
        'location': "Check Email / Campus Lounge",
        'host': "Yale Student / Department Listserv",
        'category': "Email/Listserv",
        'food_status': eval_res['food_status'],
        'food_cost': eval_res['food_cost'],
        'admission_cost': eval_res['admission_cost'],
        'confidence': eval_res['confidence'],
        'food_signals': eval_res['food_signals'],
        'evidence': eval_res['evidence'],
        'details_verified': True,
        'url': None,
        'record_kind': record_kind
    }

    if store_path:
        upsert_event(rec, store_path)
    return rec


# -----------------------------------------------------------------------------
# CLI Entrypoint
# -----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Yale Multi-Source Event Ingestion Hub")
    parser.add_argument('--luma', type=str, help='Lu.ma event URL to fetch and ingest')
    parser.add_argument('--eventbrite', type=str, help='Eventbrite event URL to fetch and ingest')
    parser.add_argument('--wechat-file', type=str, help='Path to text file containing copied WeChat article')
    parser.add_argument('--email-file', type=str, help='Path to email .eml or text file')
    parser.add_argument('--scan-emails', type=str, help='Directory of incoming emails to scan')
    parser.add_argument('--user-interest', action='store_true', help='Preserve event as user interest/community event even if food signals are absent')
    parser.add_argument('--list', action='store_true', help='List all stored supplemental events')
    parser.add_argument('--store', type=str, default=None, help='Target supplement JSON store')
    args = parser.parse_args()

    store_path = resolve_store_path(args.store)

    if args.list:
        try:
            store = load_supplement_store(store_path)
            events = store.get('events', [])
            leads = store.get('unverified_food_leads', [])
            print(f"=== Supplemental Events ({len(events)}) ===")
            for ev in events:
                print(f"- [{ev.get('id')}] {ev.get('title')} ({ev.get('food_status')}/{ev.get('food_cost')})")
            print(f"=== Unverified Food Leads ({len(leads)}) ===")
            for ld in leads:
                print(f"- [{ld.get('id')}] {ld.get('title')}")
            return
        except Exception as e:
            print(f"[ERROR] Failed to list store: {e}", file=sys.stderr)
            sys.exit(1)

    try:
        if args.luma:
            record = parse_luma_url(args.luma, store_path, allow_non_food=args.user_interest, user_interest=args.user_interest)
            if record:
                print(f"[OK] Ingested Lu.ma event: {record['title']} ({record['id']})")
            else:
                print(f"[INFO] No food signals found on Lu.ma page; skipped.")

        if args.eventbrite:
            record = parse_eventbrite_url(args.eventbrite, store_path, allow_non_food=args.user_interest, user_interest=args.user_interest)
            if record:
                print(f"[OK] Ingested Eventbrite event: {record['title']} ({record['id']})")
            else:
                print(f"[INFO] No food signals found on Eventbrite page; skipped.")

        if args.wechat_file:
            with open(args.wechat_file, 'r', encoding='utf-8') as f:
                content = f.read()
            record = parse_wechat_notice(content, store_path=store_path, allow_non_food=args.user_interest, user_interest=args.user_interest)
            if record:
                print(f"[OK] Ingested WeChat notice: {record['title']} ({record['id']})")
            else:
                print(f"[INFO] No food signals found in WeChat notice; skipped.")

        if args.email_file:
            with open(args.email_file, 'r', encoding='utf-8') as f:
                content = f.read()
            record = parse_email_text(content, store_path=store_path)
            if record:
                print(f"[OK] Ingested Email announcement: {record['title']} ({record['id']})")
            else:
                print(f"[INFO] Email filtered or no food signals found; skipped.")

        if args.scan_emails:
            s_dir = Path(args.scan_emails)
            if s_dir.exists():
                count = 0
                for fpath in s_dir.glob('*'):
                    if fpath.is_file() and not fpath.name.startswith('.'):
                        with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                            rec = parse_email_text(f.read(), store_path=store_path)
                            if rec:
                                count += 1
                print(f"[OK] Scanned emails in {s_dir}: {count} leads ingested.")
    except NetworkFetchError as e:
        print(f"[ERROR] Network fetch failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Ingestion failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
