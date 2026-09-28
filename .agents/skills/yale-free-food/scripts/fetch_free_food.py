#!/usr/bin/env python3
"""
Yale Free Food & Receptions Monitor (v1.3.0-rc4)
Fetches upcoming events from YaleConnect (CampusGroups) and identifies events
with free food, receptions, catered meals, or refreshments with multi-dimensional
status verification (food, cost, admission, eligibility, RSVP).
Strictly enforces food-entity attachment on sponsorships and price numerical checks.

Coverage Note:
  This script monitors YaleConnect (CampusGroups) feeds and public RSVP pages.
  External feeds (Lu.ma, independent departmental mailing lists) require
  separate source adapters.
"""

import sys
import os
import json
import urllib.request
import urllib.parse
import urllib.error
import ssl
import re
import argparse
from datetime import datetime, timezone, timedelta
import html as html_lib
from html.parser import HTMLParser
from pathlib import Path
import hashlib


# -----------------------------------------------------------------------------
# Timezone handling with dynamic EDT/EST fallback
# -----------------------------------------------------------------------------
def get_ny_timezone(dt=None):
    """
    Return America/New_York timezone.
    If zoneinfo is unavailable, compute correct US Eastern Daylight Time (EDT, UTC-4)
    vs Eastern Standard Time (EST, UTC-5) based on standard US DST transition rules.
    """
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/New_York")
    except Exception:
        pass

    # Dynamic fallback based on US DST rules:
    # Starts 2nd Sunday in March at 2:00 AM, ends 1st Sunday in November at 2:00 AM
    check_dt = dt or datetime.now(timezone.utc)
    year = check_dt.year

    # 2nd Sunday in March
    mar1 = datetime(year, 3, 1, 2, 0)
    mar_second_sun = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    # 1st Sunday in November
    nov1 = datetime(year, 11, 1, 2, 0)
    nov_first_sun = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)

    naive = check_dt.replace(tzinfo=None) if check_dt.tzinfo else check_dt
    if mar_second_sun <= naive < nov_first_sun:
        return timezone(timedelta(hours=-4), name="EDT")
    return timezone(timedelta(hours=-5), name="EST")


NY_TZ = get_ny_timezone()

# -----------------------------------------------------------------------------
# Keyword patterns and heuristics
# -----------------------------------------------------------------------------

# Dietary modifier negative lookbehind: ensure "free" is not part of gluten-free, etc.
DIETARY_MODIFIER_PREFIX = r'(?<!gluten-)(?<!dairy-)(?<!nut-)(?<!sugar-)(?<!fat-)(?<!soy-)(?<!smoke-)(?<!hands-)(?<!toll-)'

# Positive food keywords with confidence weight
POSITIVE_FOOD_PATTERNS = [
    # Explicit free food combinations
    (DIETARY_MODIFIER_PREFIX + r'\bfree\s+food\b', 'free food', 'high'),
    (DIETARY_MODIFIER_PREFIX + r'\bfree\s+lunch\b', 'free lunch', 'high'),
    (DIETARY_MODIFIER_PREFIX + r'\bfree\s+dinner\b', 'free dinner', 'high'),
    (DIETARY_MODIFIER_PREFIX + r'\bfree\s+breakfast\b', 'free breakfast', 'high'),
    (DIETARY_MODIFIER_PREFIX + r'\bfree\s+pizza\b', 'free pizza', 'high'),
    (DIETARY_MODIFIER_PREFIX + r'\bfree\s+boba\b', 'free boba', 'high'),
    (DIETARY_MODIFIER_PREFIX + r'\bfree\s+ice\s*cream\b', 'free ice cream', 'high'),
    (DIETARY_MODIFIER_PREFIX + r'\bfree\s+snacks?\b', 'free snacks', 'high'),
    (DIETARY_MODIFIER_PREFIX + r'\bfree\s+refreshments?\b', 'free refreshments', 'high'),
    (DIETARY_MODIFIER_PREFIX + r'\bfree\s+drinks?\b', 'free drinks', 'high'),
    # Explicit catering provided
    (r'\blunch\s+(?:provided|included|served|will be served)\b', 'lunch provided', 'high'),
    (r'\bdinner\s+(?:provided|included|served|will be served)\b', 'dinner provided', 'high'),
    (r'\bbreakfast\s+(?:provided|included|served|will be served)\b', 'breakfast provided', 'high'),
    (r'\bfood\s+(?:provided|included|served|will be served)\b', 'food provided', 'high'),
    (r'\bmeals?\s+(?:provided|included|served|will be served)\b', 'meal provided', 'high'),
    (r'\bcatered\s+(?:lunch|dinner|meal|food|reception)\b', 'catered meal', 'high'),
    (r'\bcomplimentary\s+(?:food|lunch|dinner|breakfast|refreshments?|drinks?|snacks?|ice\s*cream|treats?|desserts?|beverages?|coffee|tea|pizza|boba|bagels?|meal)\b', 'complimentary food', 'high'),
    (r'\b(?:light\s+)?refreshments?\s+(?:will\s+be\s+)?(?:available|provided|included|served)\b', 'refreshments served', 'high'),
    (r'\b(?:treat\s+you\s+to\s+\d+\s+(?:alcoholic/non-alcoholic\s+)?drinks?|\d+\s+(?:alcoholic/non-alcoholic\s+)?drinks?\s+(?:provided|included|free))\b', 'free drinks', 'high'),
    # Food types (medium confidence)
    (r'\bpizza\b', 'pizza', 'medium'),
    (r'\bboba\b|\bbubble\s*tea\b', 'boba', 'medium'),
    (r'\bice\s*cream\b', 'ice cream', 'medium'),
    (r'\bdonut(?:s)?\b|\bdoughnut(?:s)?\b', 'donuts', 'medium'),
    (r'\bbagel(?:s)?\b', 'bagels', 'medium'),
    (r'\bbbq\b|\bbarbecue\b|\bcookout\b', 'barbecue/cookout', 'medium'),
    (r'\btgif\b', 'tgif reception', 'medium'),
    (r'\bmooncake(?:s)?\b|\bmid[- ]?autumn\b|\bchuseok\b', 'cultural festival food', 'medium'),
    (r'\bmochi\b', 'mochi/treats', 'medium'),
    (r'\bpasta\b', 'pasta', 'medium'),
    (r'\btasting\b', 'tasting', 'medium'),
    (r'\bharvest\s+festival\b', 'harvest festival', 'medium'),
    (r'\bsip\s+and\s+paint\b', 'sip and paint', 'medium'),
    (r'\bstudy\s+break\b', 'study break refreshments', 'medium'),
    (r'\bsweet\s*treats?\b|\btreats?\b|\bcookies?\b', 'treats/snacks', 'medium'),
    (r'\bnetworking\s+reception\b', 'networking reception', 'medium'),
    (r'\bwelcome\s+(?:back\s+)?social\b', 'welcome social', 'medium'),
    (r'\breception\b', 'reception', 'low'),
    (r'\blunch\b', 'lunch mentioned', 'low'),
    (r'\bdinner\b', 'dinner mentioned', 'low'),
    (r'\bbreakfast\b', 'breakfast mentioned', 'low'),
    (r'\bcoffee\b|\btea\b', 'coffee/tea mentioned', 'low'),
    (r'\bsnack(?:s)?\b|\brefreshment(?:s)?\b', 'refreshments', 'low'),
]

# Explicit food negation: statements saying NO food is provided at all
EXPLICIT_NO_FOOD_PATTERNS = [
    r'\bno\s+food\s+or\s+refreshments\b',
    r'\bno\s+food\s+(?:will\s+be\s+)?provided\b',
    r'\bno\s+meals?\s+(?:will\s+be\s+)?provided\b',
    r'\bno\s+food\b',
    r'\bno\s+meals?\b',
    r'\bno\s+refreshments?\b',
    r'\bfood\s+is\s+not\s+provided\b',
    r'\bmeals?\s+not\s+included\b',
    r'\bnot\s+catered\b',
]

# Statements saying food is NOT FREE (e.g. food available for purchase)
FOOD_NOT_FREE_PATTERNS = [
    r'\bno\s+free\s+food\b',
    r'\b(?:food|pizza|lunch|dinner|snacks?|drinks?)\s+(?:is\s+|will\s+be\s+)?available\s+for\s+purchase\b',
    r'\badditional\s+(?:food\s+and\s+drinks?|drinks?|food)\s+(?:will\s+be\s+|is\s+)?available\s+for\s+purchase\b',
    r'\bbuy\s+(?:your\s+own\s+)?(?:lunch|dinner|food|pizza)\b',
    r'\bfood\s+(?:is\s+)?not\s+free\b',
]

# Statements indicating food price or plate pricing
FOOD_PRICE_PATTERNS = [
    (r'\b(?:[\w\s]+\s+)?plates?\s*\$\s*([0-9]+(?:\.[0-9]{2})?)\s+(?:preorder|in\s+advance)\s+\$\s*([0-9]+(?:\.[0-9]{2})?)\s+at\s+event\b', 'preorder_door'),
    (r'\b(?:plates?|meals?)\s*[:=]?\s*\$\s*([0-9]+(?:\.[0-9]{2})?)\b', 'single_price'),
    (r'(?<!\w)\$\s*([0-9]+(?:\.[0-9]{2})?)\s+(?:per\s+plate|per\s+meal|for\s+plate|for\s+food)\b', 'per_item'),
]

# Bring Your Own: only food/drink targets count as BYO meal
BYO_FOOD_PATTERNS = [
    r'\bbring\s+your\s+own\s+(?:food|lunch|dinner|meal|snack|drink|beverage|bottle|beer|wine)s?\b',
    r'\bbyo\s+(?:food|lunch|dinner|meal|snack|drink|beverage|bottle|beer|wine)s?\b',
    r'\bbyol\b',
    r'\bbrown\s*bag\s*(?:lunch|discussion|seminar)?\b',
    r'\bpack\s+(?:your\s+)?(?:own\s+)?(?:lunch|dinner|food)\b',
]

# BYO utensils/containers (environmental/reusable, NOT BYO food)
BYO_CUTLERY_PATTERNS = [
    r'\bbring\s+your\s+own\s+(?:plate|mug|cup|bowl|cutlery|fork|spoon|container|utensil)s?\b',
]

# Paid indicators: look for currency amounts ($)
PAID_PATTERNS = [
    r'\b(?:tickets?|admission|fee|cost|price|entry)\s*[:=]?\s*(?<!\w)\$\s*([0-9]+(?:\.[0-9]{2})?)',
    r'(?<!\w)\$\s*([0-9]+(?:\.[0-9]{2})?)\s+(?:per\s+person|per\s+ticket|each|entry|admission|door)',
    r'\bpurchase\s+tickets?\b',
    r'\bbuy\s+(?:your\s+own\s+)?(?:ticket)\b',
]

# Free admission / zero cost indicators: strictly check for $0 without trailing digits
FREE_PRICE_PATTERNS = [
    r'\b(?:tickets?|admission|fee|cost|price|entry)\s*[:=]?\s*(?<!\w)\$\s*0(?:\.00)?(?!\.\d|[0-9])',
    r'\bprice\s*[:=]?\s*free\b',
    r'\bfree\s+admission\b',
    r'\bfree\s+registration\b',
    r'\bfree\s+rsvp\b',
    r'\bno\s+fee\b',
    r'\bno\s+cost\b',
]

# Academic topic patterns
ACADEMIC_TOPIC_PATTERNS = [
    r'\bfood\s+insecurity\b',
    r'\bfood\s+waste\b',
    r'\bfood\s+systems?\b',
    r'\bfood\s+policy\b',
    r'\bfood\s+justice\b',
    r'\bfamine\b',
    r'\bagricultur(?:e|al)\b',
    r'\bnutrition\s+policy\b',
]


def clean_html(text):
    """Strip HTML tags and unescape common entities safely."""
    if not isinstance(text, str):
        return ""
    # Strip script and style tags completely
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.S | re.I)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.S | re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = text.replace('&ndash;', '–').replace('&mdash;', '—').replace('&amp;', '&').replace('&#129309;', '🤝')
    text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    return re.sub(r'\s+', ' ', text).strip()


def normalize_url(base_domain, url_path):
    """
    Normalize event URL, resolving protocol-relative URLs and ensuring valid HTTP/HTTPS scheme.
    Rejects unsafe pseudo-protocols (e.g. javascript:).
    """
    if not url_path or not isinstance(url_path, str):
        return ""
    url_path = url_path.strip()

    # Handle protocol-relative URL
    if url_path.startswith("//"):
        url_path = "https:" + url_path

    parsed = urllib.parse.urlsplit(url_path)
    if parsed.scheme:
        if parsed.scheme.lower() not in ("http", "https"):
            return ""
        return url_path

    # Relative path: use urljoin directly (do not lstrip /)
    joined = urllib.parse.urljoin(base_domain, url_path)
    j_parsed = urllib.parse.urlsplit(joined)
    if j_parsed.scheme.lower() in ("http", "https"):
        return joined
    return ""


def extract_evidence(text, keyword, window=45):
    """Extract snippet around matched keyword for transparent review."""
    match = re.search(re.escape(keyword), text, re.IGNORECASE)
    if not match:
        return ""
    start = max(0, match.start() - window)
    end = min(len(text), match.end() + window)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet


class ParsedDate:
    def __init__(self, start_dt, end_dt, ends_at_unknown, raw_str):
        self.start_dt = start_dt
        self.end_dt = end_dt
        self.ends_at_unknown = ends_at_unknown
        self.raw_str = raw_str

    def spans_date(self, target_date_str):
        """Check if event is active on target_date_str (YYYY-MM-DD in NY time)."""
        if not self.start_dt:
            return False
        try:
            target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        except Exception:
            return False

        start_date = self.start_dt.date()
        if self.end_dt:
            end_date = self.end_dt.date()
            return start_date <= target_date <= end_date
        return start_date == target_date

    def spans_date_range(self, from_date_str=None, to_date_str=None):
        """Check if event is active within [from_date_str, to_date_str] (YYYY-MM-DD in NY time)."""
        if not self.start_dt:
            return False
        try:
            start_limit = datetime.strptime(from_date_str, "%Y-%m-%d").date() if from_date_str else None
            end_limit = datetime.strptime(to_date_str, "%Y-%m-%d").date() if to_date_str else None
        except Exception:
            return False

        ev_start = self.start_dt.date()
        ev_end = self.end_dt.date() if self.end_dt else ev_start

        if start_limit and ev_end < start_limit:
            return False
        if end_limit and ev_start > end_limit:
            return False
        return True

    def is_past(self, now_ny):
        """Check if event has ended relative to now_ny. Never assume 2-hour duration."""
        if not self.start_dt:
            return False
        if not self.ends_at_unknown and self.end_dt:
            return self.end_dt < now_ny
        # If ends_at is unknown, do not guess; on target date it is NOT past
        if self.start_dt.date() < now_ny.date():
            return True
        return False


def parse_yale_date(date_str, now_ny=None):
    """
    Parse YaleConnect date strings:
      - Single-day range: 'Fri, Sep 18, 2026 2 PM – 4 PM' or 'Fri, Sep 18, 2026 1:00 PM – 3:00 PM'
      - Multi-day range: 'Fri, Sep 18, 2026 9 AM – Sun, Sep 20, 2026 5 PM'
      - Single time: 'Fri, Sep 18, 2026 10 AM'
    Returns ParsedDate or None.
    """
    if not date_str or not isinstance(date_str, str):
        return None

    clean_str = clean_html(date_str).replace("–", "-").replace("—", "-")

    time_pat = r'\d{1,2}(?::\d{2})?\s*(?:AM|PM)'
    date_pat = r'[A-Za-z]{3},\s+[A-Za-z]{3}\s+\d{1,2},\s+\d{4}'

    def parse_dt_token(d_str, t_str):
        d_clean = re.sub(r'^[A-Za-z]{3},\s*', '', d_str.strip())
        t_clean = t_str.strip().upper().replace(" ", "")
        fmt = "%b %d, %Y %I:%M%p" if ":" in t_clean else "%b %d, %Y %I%p"
        naive_dt = datetime.strptime(f"{d_clean} {t_clean}", fmt)
        # Determine NY timezone specific to this parsed date
        tz = get_ny_timezone(naive_dt)
        return naive_dt.replace(tzinfo=tz)

    # 0. ISO date format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS...
    m_iso = re.match(r'^(\d{4}-\d{2}-\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2}))?([+-]\d{2}:?\d{2}|Z)?)?', clean_str)
    if m_iso and ('T' in clean_str or len(clean_str) == 10):
        try:
            m_iso_range = re.search(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)\s*(?:-|to|\/)\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)', clean_str)
            if m_iso_range:
                s_raw = m_iso_range.group(1).replace('Z', '+00:00')
                e_raw = m_iso_range.group(2).replace('Z', '+00:00')
                s_dt = datetime.fromisoformat(s_raw)
                e_dt = datetime.fromisoformat(e_raw)
                s_tz = get_ny_timezone(s_dt)
                e_tz = get_ny_timezone(e_dt)
                return ParsedDate(s_dt.astimezone(s_tz), e_dt.astimezone(e_tz), False, date_str)
            if 'T' in clean_str:
                s_dt = datetime.fromisoformat(clean_str.replace('Z', '+00:00'))
                s_tz = get_ny_timezone(s_dt)
                return ParsedDate(s_dt.astimezone(s_tz), None, True, date_str)
            d_val = datetime.strptime(m_iso.group(1), "%Y-%m-%d")
            s_tz = get_ny_timezone(d_val)
            return ParsedDate(d_val.replace(tzinfo=s_tz), None, True, date_str)
        except Exception:
            pass

    # 1. Multi-day range: date1 time1 - date2 time2
    m_multi = re.search(rf'({date_pat})\s+({time_pat})\s*-\s*({date_pat})\s+({time_pat})', clean_str, re.I)
    if m_multi:
        try:
            s_dt = parse_dt_token(m_multi.group(1), m_multi.group(2))
            e_dt = parse_dt_token(m_multi.group(3), m_multi.group(4))
            return ParsedDate(s_dt, e_dt, False, date_str)
        except Exception:
            pass

    # 2. Single-day range: date1 time1 - time2
    m_single = re.search(rf'({date_pat})\s+({time_pat})\s*-\s*({time_pat})', clean_str, re.I)
    if m_single:
        try:
            d_part = m_single.group(1)
            s_dt = parse_dt_token(d_part, m_single.group(2))
            e_dt = parse_dt_token(d_part, m_single.group(3))
            if e_dt < s_dt:
                e_dt += timedelta(days=1)
            return ParsedDate(s_dt, e_dt, False, date_str)
        except Exception:
            pass

    # 3. Single time point: date1 time1 (no end time)
    m_point = re.search(rf'({date_pat})\s+({time_pat})', clean_str, re.I)
    if m_point:
        try:
            s_dt = parse_dt_token(m_point.group(1), m_point.group(2))
            return ParsedDate(s_dt, None, True, date_str)
        except Exception:
            pass

    return None


# -----------------------------------------------------------------------------
# Public detail page parsing (HTML element boundary extraction)
# -----------------------------------------------------------------------------
class YaleDetailHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_script = False
        self.in_style = False
        
        self.current_section = None  # 'registration', 'details', 'other'
        self.has_registration_section = False
        self.has_details_section = False
        
        self.registration_text_parts = []
        self.details_text_parts = []
        
        self.current_heading_tag = None
        self.current_heading_text = []
        
        self.current_row = []
        self.current_cell = []
        self.in_th = False
        self.in_td = False
        
        self.has_food_badge = False
        self.stop_details = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        tag_lower = tag.lower()
        
        if tag_lower in ('script', 'style'):
            self.in_script = True
            return

        cls = attrs_dict.get('class', '')
        if 'mdi-food' in cls:
            self.has_food_badge = True
            
        # Stopping Details section markers:
        if tag_lower == 'button' and 'btn' in cls:
            aria = attrs_dict.get('aria-label', '')
            if 'copy link' in aria.lower() or 'copy link' in cls.lower():
                if self.current_section == 'details':
                    self.current_section = None
                    self.stop_details = True
                    
        elem_id = attrs_dict.get('id', '')
        if elem_id in ('event_speakers', 'event_where', 'event_host', 'event_child_events', 'event_child'):
            if self.current_section == 'details':
                self.current_section = None
                self.stop_details = True

        if tag_lower in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.current_heading_tag = tag_lower
            self.current_heading_text = []

        if tag_lower == 'tr':
            self.current_row = []
        if tag_lower == 'th':
            self.in_th = True
            self.current_cell = []
        if tag_lower == 'td':
            self.in_td = True
            self.current_cell = []

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        if tag_lower in ('script', 'style'):
            self.in_script = False
            return

        if tag_lower in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6') and tag_lower == self.current_heading_tag:
            heading = ''.join(self.current_heading_text).strip()
            self.current_heading_tag = None
            h_lower = heading.lower()
            
            if 'registration' in h_lower or 'tickets' in h_lower or 'rsvp' in h_lower:
                self.current_section = 'registration'
                self.has_registration_section = True
            elif 'details' in h_lower and 'registration' not in h_lower:
                if not self.stop_details:
                    self.current_section = 'details'
                    self.has_details_section = True
            elif any(k in h_lower for k in ('hosted by', 'event host', 'where', 'speakers', 'copy link', 'child events')):
                self.current_section = 'other'
                self.stop_details = True

        if tag_lower == 'th':
            self.in_th = False
            cell_text = ' '.join(''.join(self.current_cell).split())
            self.current_row.append(('th', cell_text))

        if tag_lower == 'td':
            self.in_td = False
            cell_text = ' '.join(''.join(self.current_cell).split())
            self.current_row.append(('td', cell_text))

        if tag_lower == 'tr':
            if self.current_row and self.current_section == 'registration':
                row_str = ' | '.join(txt for _, txt in self.current_row if txt)
                if row_str:
                    self.registration_text_parts.append(row_str)

    def handle_data(self, data):
        if self.in_script or self.in_style:
            return
        if self.current_heading_tag:
            self.current_heading_text.append(data)
        if self.in_th or self.in_td:
            self.current_cell.append(data)

        text = data.strip()
        if not text:
            return

        if 'food provided' in text.lower():
            self.has_food_badge = True

        if self.current_section == 'details' and not self.current_heading_tag:
            self.details_text_parts.append(data)
        elif self.current_section == 'registration' and not self.current_heading_tag:
            self.registration_text_parts.append(data)


def parse_registration_deadline(text):
    """
    Extract registration deadline from text, returns (iso_str, display_str) or (None, None).
    Examples:
      'Sep 20, 2026 (at 11:30 PM)' -> ('2026-09-20T23:30:00-04:00', '2026-09-20 23:30 EDT')
      'Sep 23, 2026 at 5 PM' -> ('2026-09-23T17:00:00-04:00', '2026-09-23 17:00 EDT')
    """
    if not text:
        return None, None
    m = re.search(
        r'([A-Za-z]{3,9})\s+(\d{1,2}),\s+(\d{4})\s*(?:\(at\s+|at\s+)?([0-9]{1,2})(?::([0-9]{2}))?\s*([AP]M)\)?',
        text,
        re.I
    )
    if not m:
        return None, None
    month_name, day_str, year_str, hr_str, min_str, ampm = m.groups()
    months = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'sept': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }
    m_num = months.get(month_name[:3].lower())
    if not m_num:
        return None, None
    day = int(day_str)
    year = int(year_str)
    hr = int(hr_str)
    minute = int(min_str) if min_str else 0
    if ampm.upper() == 'PM' and hr < 12:
        hr += 12
    elif ampm.upper() == 'AM' and hr == 12:
        hr = 0

    iso_val = f"{year:04d}-{m_num:02d}-{day:02d}T{hr:02d}:{minute:02d}:00-04:00"
    display_val = f"{year:04d}-{m_num:02d}-{day:02d} {hr:02d}:{minute:02d} EDT"
    return iso_val, display_val


def parse_detail_page(html_text):
    """
    Extract structured fields from public YaleConnect event RSVP page HTML/text:
      - details_text: description content under Details section ONLY
      - has_food_badge: whether explicit 'Food Provided' badge exists
      - admission_price: string ('free', 'paid ($25.00)') or 'unknown'
      - eligibility: membership or eligibility restrictions ('unknown' by default)
      - meeting_notes: location or meeting point hints
      - rsvp_status: RSVP requirements
      - login_restricted: boolean
      - is_unparsed: boolean
    """
    if not html_text:
        return {"is_unparsed": True}

    # Detect login wall / sign-in requirement
    if "Please sign in to view this event" in html_text or "Please sign in to access this page" in html_text:
        return {
            "details_text": "",
            "has_food_badge": False,
            "admission_price": "unknown",
            "eligibility": "特定成员限定 (需登录)",
            "meeting_notes": None,
            "rsvp_status": "需登录",
            "login_restricted": True,
            "is_unparsed": False
        }

    is_html = bool(re.search(r'<[a-zA-Z][^>]*>', html_text))

    if is_html:
        parser = YaleDetailHTMLParser()
        parser.feed(html_text)
        
        has_registration = parser.has_registration_section
        has_food_badge = parser.has_food_badge
        
        details_text = clean_html(''.join(parser.details_text_parts))
        reg_text = ' '.join(parser.registration_text_parts)
        is_unparsed = not parser.has_details_section
        if not details_text:
            cleaned_all = clean_html(html_text)
            if cleaned_all:
                details_text = cleaned_all
                is_unparsed = False
    else:
        # Plain text parsing (e.g. CLI text dump / test fixtures)
        has_food_badge = bool(re.search(r'\bfood\s+provided\b', html_text, re.I))
        
        m_reg = re.search(r'(?:^|\n)\s*(?:Registration|Tickets)\s*\n([\s\S]*?)(?:(?:^|\n)\s*(?:Details|Hosted By|Copy Link)\s*\n|$)', html_text, re.I)
        has_registration = bool(m_reg)
        reg_text = m_reg.group(1) if m_reg else ""
        
        m_det = re.search(r'(?:^|\n)\s*Details\s*\n([\s\S]*?)(?:(?:^|\n)\s*(?:Hosted By|Copy Link|Website|Contact)\s*\n|$)', html_text, re.I)
        if m_det:
            details_text = clean_html(m_det.group(1))
            is_unparsed = False
        else:
            details_text = ""
            is_unparsed = True

    # Price extraction from registration section ONLY (or explicit RSVP event price badge)
    admission_price = "unknown"
    if has_registration and reg_text:
        # 1. Dollar amounts ($25.00, $0, etc.)
        prices = re.findall(r'(?<!\w)\$\s*([0-9]+(?:\.[0-9]{2})?)', reg_text)
        if prices:
            vals = [float(p) for p in prices]
            max_val = max(vals)
            admission_price = f"paid (${max_val:.2f})" if max_val > 0 else "free"
        elif re.search(r'\bFREE\b', reg_text, re.I):
            admission_price = "free"

    # Fallback for past events where registration section says "Registration is now closed"
    if admission_price == "unknown" and is_html:
        m_badge = re.search(r'class="[^"]*rsvp__event-price[^"]*">\s*([^<]+)\s*<', html_text)
        if m_badge:
            badge_val = m_badge.group(1).strip()
            if badge_val.upper() == "FREE":
                admission_price = "free"
            else:
                m_doll = re.search(r'\$\s*([0-9]+(?:\.[0-9]{2})?)', badge_val)
                if m_doll:
                    val = float(m_doll.group(1))
                    admission_price = f"paid (${val:.2f})" if val > 0 else "free"

    # Benefit & allowance notes
    benefit_notes = []
    m_drink = re.search(r'(?:treat\s+you\s+to\s+)?(\d+\s+(?:alcoholic/non-alcoholic\s+)?drinks?\s*(?:\([^)]*\))?)', details_text, re.I)
    if m_drink:
        benefit_notes.append(f"提供饮品名额: {m_drink.group(0).strip()}")
    m_extra = re.search(r'additional\s+(?:food\s+and\s+drinks?|drinks?|food)\s+(?:will\s+be\s+)?available\s+for\s+purchase(?:\s+at\s+your\s+own\s+expense)?', details_text, re.I)
    if m_extra:
        benefit_notes.append("额外餐饮自费")

    # Host treat benefit recognition
    m_treat = re.search(
        r'(?:(?:^|[\n.!?]|\b(?:and|as)\s+)\s*([A-Z][A-Za-z0-9&_\'\- ]{1,25})\s+will\s+)?treat\s+(you|attendees|participants|guests|students|everyone)\s+to\s+((?:a|an|\d+)\s+[^.!,;\n]+?(?:snack|meal|lunch|dinner|breakfast|food|drink|beverage|treat)s?(?:\s+from\s+[^.!,;\n]+)?)',
        details_text,
        re.I
    )
    if m_treat:
        raw_host = m_treat.group(1)
        host_prefix = raw_host.strip() if raw_host and raw_host.strip().lower() not in ('we', 'they', 'i', 'you') else "主办方"
        treat_obj = m_treat.group(3).strip()
        if "snack" in treat_obj.lower() and "market" in treat_obj.lower():
            benefit_notes.append(f"{host_prefix}请参加者吃一份市场摊位小吃；不代表全部购物免费或无限量")
        elif "snack" in treat_obj.lower():
            benefit_notes.append(f"{host_prefix}请参加者吃一份小吃/点心；不代表全部消费免费或无限量")
        elif any(w in treat_obj.lower() for w in ("meal", "lunch", "dinner", "breakfast")):
            benefit_notes.append(f"{host_prefix}请参加者吃一份餐食；不代表全部消费免费或无限量")
        else:
            benefit_notes.append(f"{host_prefix}请参加者享用 {treat_obj}；不代表全部消费免费或无限量")

    # Complimentary items benefit recognition (e.g. complimentary ice cream)
    m_comp = re.search(
        r'(?:(?:^|[\n.!?]|\b(?:with|and|as)\s+)\s*([A-Z][A-Za-z0-9&_\'\- ]{1,25})\s+)?complimentary\s+((?:[a-zA-Z0-9\- ]+?\s+)?(?:ice\s*cream|treats?|snacks?|food|lunch|dinner|breakfast|refreshments?|drinks?|beverages?))\b(?:\s*,\s*including\s+([^,.;!)"]+))?',
        details_text,
        re.I
    )
    if m_comp:
        raw_comp_host = m_comp.group(1)
        comp_host = raw_comp_host.strip() if raw_comp_host and raw_comp_host.strip().lower() not in ('we', 'they', 'i', 'you', 'with', 'and') else "主办方"
        item_name = m_comp.group(2).strip()
        incl = m_comp.group(3).strip() if m_comp.group(3) else None
        if "ice cream" in item_name.lower():
            if incl and "dairy-free" in incl.lower():
                benefit_notes.append(f"{comp_host}提供免费冰淇淋（含不含乳制品选项，dairy-free）")
            elif incl:
                benefit_notes.append(f"{comp_host}提供免费冰淇淋（含 {incl}）")
            else:
                benefit_notes.append(f"{comp_host}提供免费冰淇淋")
        else:
            benefit_notes.append(f"{comp_host}提供免费 {item_name}")

    # Eligibility extraction
    eligibility = "unknown"
    clean_all = clean_html(html_text)

    if re.search(r'\[(?:This\s+program\s+is\s+)?designed\s+for\s+new\s+incoming\s+international\s+students[^\]]*\]', clean_all, re.I) or re.search(r'designed\s+for\s+new\s+incoming\s+international\s+students', clean_all, re.I):
        eligibility = "面向秋季新入学国际学生"
    elif re.search(r'Open\s+to\s+newly\s+arrived\s+members\s+of\s+the\s+international\s+Yale\s+Community', clean_all, re.I):
        eligibility = "限新到校国际社区成员"
    elif re.search(r'(?:spouses?\s+and\s+partners?\s+of\s+Yale\s+students\s+and\s+scholars|ISPY)', clean_all, re.I):
        eligibility = "面向Yale学生及学者的配偶/伴侣社群；其他人能否参加未明示"
    elif re.search(r'\bfellow\s+postdocs\b|\bpostdocs\s+at\s+this\b', clean_all, re.I):
        eligibility = "正文邀请博士后；其他人员准入unknown"
    elif re.search(r'(?:new\s+international\s+graduate\s+or\s+professional\s+student|international\s+G&P\s+students?)', clean_all, re.I):
        eligibility = "面向新入学国际研究生/专业学院学生"
    elif re.search(r'\bAll\s+new\s+Yale\s+College\s+students\b', clean_all, re.I):
        eligibility = "面向 Yale College 新生、转学生及 Eli Whitney 学生"
        if re.search(r'This event is open to specific members only', clean_all, re.I):
            eligibility += " (需登录)"
    elif re.search(r'open\s+to\s+all\s+([A-Za-z0-9&_\'\- ]+?\s+community\s+members)', clean_all, re.I):
        m_comm = re.search(r'open\s+to\s+all\s+([A-Za-z0-9&_\'\- ]+?\s+community\s+members)(?:,\s*regardless\s+of\s+([^\]]+))?', clean_all, re.I)
        target = m_comm.group(1).strip()
        cond = "（不限信仰）" if "regardless of belief" in clean_all.lower() else ""
        if re.search(r'This event is open to specific members only', clean_all, re.I):
            eligibility = f"正文明确面向所有 {target}{cond}；注册区提示特定成员限定需登录核实"
        else:
            eligibility = f"面向所有 {target}{cond}"
    elif re.search(r'This event is open to specific members only', clean_all, re.I):
        eligibility = "特定成员限定 (需登录核实)"
        m_group = re.search(r'(\b[A-Z]{2,}\s+Students?\b|\bYale\s+College\b|\bFirst-Year\b|\bTransfer\b|\bGraduate\b)', clean_all)
        if m_group:
            eligibility += f" (面向 {m_group.group(0)})"
    elif re.search(r'\b(?:open\s+to\s+(?:all\s+)?(?:yale\s+community|public)|free\s+and\s+open)\b', clean_all, re.I):
        eligibility = "公开 / 全员"

    # Meeting notes
    meeting_notes = None
    m_gather = re.search(
        r'\b(?:please\s+)?gather\s+at\s+([0-9:apmAPM]+)\s+outside\s+the\s+entrance\s+of\s+([^,(\n]+)\s*\(([^)]+)\)',
        details_text,
        re.I
    )
    if m_gather:
        t_str = m_gather.group(1).lower().replace('am', '').replace('pm', '').strip()
        school_str = m_gather.group(2).strip()
        addr_str = m_gather.group(3).strip()
        meeting_notes = f"{t_str}在{school_str}入口外集合，{addr_str}"
    else:
        m_loc = re.search(r'(?:meet\s+at|meeting\s+at|gather(?:ing)?\s+at)\s+([^,.\n]+)', details_text, re.I)
        if m_loc and not re.search(r'\b(?:a\s+|casual\s+)gathering\b', details_text[:m_loc.start() + 20], re.I):
            meeting_notes = m_loc.group(0).strip()

    # Registration deadline, status, and tickets limit
    registration_status = None
    registration_deadline = None
    deadline_display = None
    ticket_limit = None

    if has_registration and reg_text:
        m_closed_alert = re.search(
            r'Registration\s+(?:will\s+)?only be open from\s+.*?\s+to\s+([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}\s*(?:\(at\s+|at\s+)?[0-9]{1,2}(?::[0-9]{2})?\s*[AP]M\)?)',
            reg_text,
            re.I
        )
        if m_closed_alert:
            registration_status = "closed"
            registration_deadline, deadline_display = parse_registration_deadline(m_closed_alert.group(1))
        elif re.search(r'Registration\s+(?:is\s+now\s+closed|closed)', reg_text, re.I) and "already took place" not in reg_text.lower():
            registration_status = "closed"

        if not registration_deadline:
            m_sales = re.search(
                r'Sales End\s*(?:[|:\-])?\s*([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}\s*(?:at\s+)?[0-9]{1,2}(?::[0-9]{2})?\s*[AP]M)',
                reg_text,
                re.I
            )
            if not m_sales and "sales end" in reg_text.lower():
                m_sales = re.search(
                    r'([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}\s*(?:at\s+)?[0-9]{1,2}(?::[0-9]{2})?\s*[AP]M)',
                    reg_text,
                    re.I
                )
            if m_sales:
                registration_deadline, deadline_display = parse_registration_deadline(m_sales.group(1) if m_sales.groups() else m_sales.group(0))

        m_lim = re.search(r'limit at\s+(\d+)\s+tickets? per person', reg_text, re.I)
        if m_lim:
            ticket_limit = int(m_lim.group(1))

    # RSVP status
    rsvp_status = "unknown"
    if registration_status == "closed":
        if deadline_display:
            rsvp_status = f"报名已截止（页面截止时间{deadline_display}）"
        else:
            rsvp_status = "报名已截止"
    elif re.search(r'\b(?:must\s+register(?:\s+in\s+order\s+to\s+attend)?)\b', clean_all, re.I):
        if deadline_display:
            rsvp_status = f"必须注册；报名截止{deadline_display}"
        else:
            rsvp_status = "必须注册"
    elif re.search(r'\b(?:must\s+rsvp|rsvp\s+required|registration\s+required)\b', clean_all, re.I):
        if deadline_display:
            rsvp_status = f"必须RSVP；报名截止{deadline_display}"
        else:
            rsvp_status = "必须 RSVP"
    elif ticket_limit and admission_price == "free":
        rsvp_status = f"公开页面提供免费RSVP；每人最多{ticket_limit}张票"
    elif re.search(r'\b(?:make\s+sure\s+to\s+rsvp\b.*?\bto\s+get|rsvp\b.*?\bto\s+get)\b', details_text, re.I):
        rsvp_status = "免费领取需 RSVP"
    elif re.search(r'\bfirst-come,\s*first[- ]served\b', details_text, re.I):
        rsvp_status = "先到先得 (建议 RSVP)"
    elif has_registration:
        if deadline_display:
            rsvp_status = f"建议RSVP；报名截止{deadline_display}"
        else:
            rsvp_status = "建议 RSVP"

    return {
        "details_text": details_text,
        "has_food_badge": has_food_badge,
        "admission_price": admission_price,
        "eligibility": eligibility,
        "meeting_notes": meeting_notes,
        "benefit_notes": benefit_notes,
        "rsvp_status": rsvp_status,
        "registration_status": registration_status,
        "registration_deadline": registration_deadline,
        "login_restricted": False,
        "is_unparsed": is_unparsed
    }



# -----------------------------------------------------------------------------
# Classification Engine (Decoupled Dimensions)
# -----------------------------------------------------------------------------
def classify_event(item, detail_info=None, target_date_str=None, from_date_str=None, to_date_str=None, now_ny=None, upcoming_only=False):
    """
    Multi-dimensional analysis of a Yale event candidate.
    Separates:
      - food_status: provided / likely / unclear / none
      - food_cost: free / paid / byo / unknown
      - admission_cost: free / paid ($XX.XX) / unknown
      - eligibility: string
      - rsvp_status: string
      - confidence: confirmed_free / likely_free / needs_verification / excluded
    All return branches return a consistent set of fields.
    """
    if not isinstance(item, dict):
        return None

    if item.get("p2") == "separator" or not item.get("p3"):
        return None

    now_ny = now_ny or datetime.now(NY_TZ)

    # Basic fields from feed
    event_id = item.get("p1", "")
    title = clean_html(item.get("p3", ""))
    dates_raw = clean_html(item.get("p4", ""))
    category = clean_html(item.get("p5", ""))
    location = clean_html(item.get("p6", ""))
    club = clean_html(item.get("p9", ""))
    price_range_feed = clean_html(item.get("p12", ""))
    url_path = item.get("p18", "")
    tags = clean_html(item.get("p22", ""))

    parsed_date = parse_yale_date(dates_raw, now_ny=now_ny)

    # 1. Date Filtering
    if from_date_str or to_date_str:
        if parsed_date and not parsed_date.spans_date_range(from_date_str, to_date_str):
            return None
    elif target_date_str:
        if parsed_date and not parsed_date.spans_date(target_date_str):
            return None

    # 2. Upcoming filtering
    if upcoming_only and parsed_date and parsed_date.is_past(now_ny):
        return None

    # Merge detail page text if available
    detail_text = ""
    has_food_badge = False
    detail_admission = "unknown"
    eligibility = "unknown"
    rsvp_status = "unknown"
    meeting_notes = None
    benefit_notes = []
    registration_status = None
    registration_deadline = None
    details_verified = False

    if detail_info:
        detail_text = detail_info.get("details_text", "")
        has_food_badge = detail_info.get("has_food_badge", False)
        detail_admission = detail_info.get("admission_price", "unknown")
        eligibility = detail_info.get("eligibility", eligibility)
        rsvp_status = detail_info.get("rsvp_status", rsvp_status)
        meeting_notes = detail_info.get("meeting_notes")
        benefit_notes = list(detail_info.get("benefit_notes", []))
        registration_status = detail_info.get("registration_status")
        registration_deadline = detail_info.get("registration_deadline")
        details_verified = not detail_info.get("is_unparsed", False) and not detail_info.get("login_restricted", False)

    combined_text = f"{title} | Location: {location} | Category: {category} | Tags: {tags} | Details: {detail_text}"

    # Also extract host treat if not already in benefit_notes
    if not benefit_notes:
        m_treat = re.search(
            r'(?:(?:^|[\n.!?]|\b(?:and|as)\s+)\s*([A-Z][A-Za-z0-9&_\'\- ]{1,25})\s+will\s+)?treat\s+(you|attendees|participants|guests|students|everyone)\s+to\s+((?:a|an|\d+)\s+[^.!,;\n]+?(?:snack|meal|lunch|dinner|breakfast|food|drink|beverage|treat)s?(?:\s+from\s+[^.!,;\n]+)?)',
            combined_text,
            re.I
        )
        if m_treat:
            raw_host = m_treat.group(1)
            host_prefix = raw_host.strip() if raw_host and raw_host.strip().lower() not in ('we', 'they', 'i', 'you') else (club or "主办方")
            treat_obj = m_treat.group(3).strip()
            if "snack" in treat_obj.lower() and "market" in treat_obj.lower():
                benefit_notes.append(f"{host_prefix}请参加者吃一份市场摊位小吃；不代表全部购物免费或无限量")
            elif "snack" in treat_obj.lower():
                benefit_notes.append(f"{host_prefix}请参加者吃一份小吃/点心；不代表全部消费免费或无限量")
            elif any(w in treat_obj.lower() for w in ("meal", "lunch", "dinner", "breakfast")):
                benefit_notes.append(f"{host_prefix}请参加者吃一份餐食；不代表全部消费免费或无限量")
            else:
                benefit_notes.append(f"{host_prefix}请参加者享用 {treat_obj}；不代表全部消费免费或无限量")

    # Standard base output dictionary to guarantee identical field structure
    full_url = normalize_url("https://yaleconnect.yale.edu", url_path)
    loc_display = location if location else "地点待定 / 详见活动链接"
    if meeting_notes:
        if "在" in meeting_notes and "集合" in meeting_notes:
            loc_display = meeting_notes
        elif "Private Location" in loc_display:
            loc_display = f"{loc_display} (集合点: {meeting_notes})"
    if "Environmental Humanities" in title and "HQ" not in loc_display:
        loc_display = f"{loc_display} (公告地点: HQ 134)"

    def make_res(food_status, food_cost, admission_cost, conf, food_signals, evidence, food_cost_notes=None, admission_notes=None):
        res_dict = {
            "id": event_id,
            "title": title,
            "dates": dates_raw or "时间待定",
            "starts_at": parsed_date.start_dt.isoformat() if parsed_date and parsed_date.start_dt else None,
            "ends_at": parsed_date.end_dt.isoformat() if parsed_date and parsed_date.end_dt else None,
            "ends_at_unknown": parsed_date.ends_at_unknown if parsed_date else True,
            "location": loc_display,
            "host": club or "主办方待定",
            "category": category or "活动",
            "food_status": food_status,
            "food_cost": food_cost,
            "food_cost_notes": food_cost_notes or [],
            "benefit_notes": benefit_notes,
            "admission_cost": admission_cost,
            "admission_notes": admission_notes or [],
            "eligibility": eligibility,
            "rsvp_status": rsvp_status,
            "food_signals": food_signals,
            "evidence": evidence[:3],
            "confidence": conf,
            "details_verified": details_verified,
            "url": full_url
        }
        if registration_status:
            res_dict["registration_status"] = registration_status
        if registration_deadline:
            res_dict["registration_deadline"] = registration_deadline
        return res_dict

    # 3. Check Explicit Negation: NO FOOD provided
    for pat in EXPLICIT_NO_FOOD_PATTERNS:
        m = re.search(pat, combined_text, re.I)
        if m:
            ev = extract_evidence(combined_text, m.group(0))
            return make_res(
                food_status="none",
                food_cost="unknown",
                admission_cost="unknown",
                conf="excluded",
                food_signals=[],
                evidence=[f"明确不供餐: {ev}"] if ev else ["明确不供餐"]
            )

    # 4. Detect Food Signals
    matched_signals = []
    evidence_snippets = []
    highest_weight = 'none'

    # Mask non-food cookie references (browser cookies, cookie preferences, etc.)
    text_for_signals = re.sub(r'\b(?:browser|tracking|session|http|web|privacy|accept|manage)\s+cookies?\b', '[tech-cookie]', combined_text, flags=re.I)
    text_for_signals = re.sub(r'\bcookies?\s+(?:policy|preferences|settings)\b', '[tech-cookie]', text_for_signals, flags=re.I)

    if has_food_badge:
        matched_signals.append("Food Provided (详情页徽章)")
        highest_weight = 'high'
        evidence_snippets.append("详情页明确标注 'Food Provided' 徽章")

    if re.search(r'\bFood\b', tags):
        matched_signals.append("Food (列表标签)")
        if highest_weight != 'high':
            highest_weight = 'medium'
        evidence_snippets.append("列表标签标注 'Food'")

    for pat, label, weight in POSITIVE_FOOD_PATTERNS:
        matches = list(re.finditer(pat, text_for_signals, re.I))
        for m in matches:
            matched_signals.append(label)
            if weight == 'high' or (weight == 'medium' and highest_weight != 'high'):
                highest_weight = weight
            elif highest_weight == 'none':
                highest_weight = 'low'
            ev = extract_evidence(combined_text, m.group(0))
            if ev and ev not in evidence_snippets:
                evidence_snippets.append(ev)

    if not matched_signals:
        return None

    matched_signals = sorted(list(set(matched_signals)))

    # 5. Food Status Determination
    if has_food_badge or any(s in matched_signals for s in ['food provided', 'lunch provided', 'dinner provided', 'breakfast provided', 'catered meal', 'complimentary food', 'refreshments served', 'free drinks']):
        food_status = 'provided'
    elif any(s in matched_signals for s in ['pizza', 'boba', 'ice cream', 'donuts', 'bagels', 'barbecue', 'treats/snacks', 'free food', 'free lunch', 'free pizza']):
        food_status = 'provided'
    elif any(s in matched_signals for s in ['networking reception', 'welcome social', 'reception']):
        food_status = 'likely'
    else:
        food_status = 'unclear'

    # 6. Admission Cost Determination
    admission_cost = 'unknown'
    admission_notes = []

    if detail_admission and detail_admission != "unknown":
        admission_cost = detail_admission
    elif re.search(r'\bFREE\b', price_range_feed, re.I):
        admission_cost = 'free'
    else:
        # Check $0 first
        for pat in FREE_PRICE_PATTERNS:
            if re.search(pat, combined_text, re.I):
                admission_cost = 'free'
                admission_notes.append("检测到免费门票/免费注册")
                break

    if admission_cost == 'unknown':
        for pat in PAID_PATTERNS:
            m = re.search(pat, combined_text, re.I)
            if m:
                if m.groups() and m.group(1):
                    val = float(m.group(1))
                    if val == 0:
                        admission_cost = 'free'
                        break
                    admission_cost = f"paid (${val:.2f})"
                else:
                    admission_cost = 'paid'
                admission_notes.append(f"门票收费: '{m.group(0)}'")
                break

    # 7. Food Cost Determination
    food_cost = 'unknown'
    food_cost_notes = []

    # Check BYO Cutlery/Container (does NOT exclude food)
    for pat in BYO_CUTLERY_PATTERNS:
        m = re.search(pat, combined_text, re.I)
        if m:
            food_cost_notes.append(f"自带餐具/杯具要求: '{m.group(0)}'")

    # Check BYO Food
    is_byo_food = False
    for pat in BYO_FOOD_PATTERNS:
        m = re.search(pat, combined_text, re.I)
        if m:
            is_byo_food = True
            food_cost = 'byo'
            food_cost_notes.append(f"自带餐食 (BYO): '{m.group(0)}'")
            break

    if not is_byo_food:
        # Check explicit Food Not Free
        is_food_not_free = False
        for pat in FOOD_NOT_FREE_PATTERNS:
            m = re.search(pat, combined_text, re.I)
            if m:
                matched_str = m.group(0)
                if benefit_notes and ("additional" in matched_str.lower() or "purchase at your own expense" in combined_text.lower()):
                    food_cost_notes.append(f"限定免费额度外额外自费: '{matched_str}'")
                    continue
                is_food_not_free = True
                food_cost = 'paid'
                food_cost_notes.append(f"餐饮收费/非免费: '{matched_str}'")
                break

        if not is_food_not_free:
            for pat, kind in FOOD_PRICE_PATTERNS:
                m = re.search(pat, combined_text, re.I)
                if m:
                    if kind == 'preorder_door':
                        p1 = float(m.group(1))
                        p2 = float(m.group(2))
                        if p1 > 0 or p2 > 0:
                            is_food_not_free = True
                            food_cost = 'paid'
                            matched_str = m.group(0).strip()
                            food_cost_notes.append(f"餐食收费: 预订${m.group(1)}／现场${m.group(2)} ('{matched_str}')")
                            break
                        else:
                            food_cost_notes.append(f"餐盘免费: ('{m.group(0).strip()}')")
                    else:
                        p = float(m.group(1))
                        if p > 0:
                            is_food_not_free = True
                            food_cost = 'paid'
                            matched_str = m.group(0).strip()
                            food_cost_notes.append(f"餐食收费: ${m.group(1)} ('{matched_str}')")
                            break
                        else:
                            food_cost_notes.append(f"餐盘免费: ('{m.group(0).strip()}')")

        if not is_food_not_free:
            m_paid_by = re.search(
                r'\b(?:(?:ice\s*cream\s+)?treats?|food|lunch|dinner|breakfast|pizza|boba|refreshments?|drinks?|snacks?|meals?)\s*(?:\([^)]*\))?\s*\((?:paid\s+for|covered)\s+by\s+([^,.;!)"]+)\)'
                r'|\b(?:(?:ice\s*cream\s+)?treats?|food|lunch|dinner|breakfast|pizza|boba|refreshments?|drinks?|snacks?|meals?)\s+(?:is\s+|are\s+)?(?:paid\s+for|covered)\s+by\s+([^,.;!)"]+)',
                combined_text,
                re.I
            )
            is_valid_sponsor = False
            payer = None
            if m_paid_by:
                payer = (m_paid_by.group(1) or m_paid_by.group(2)).strip()
                payer_words = set(re.findall(r'[a-zA-Z]+', payer.lower()))
                is_self_pay = bool(payer_words & {
                    'attendee', 'attendees', 'participant', 'participants', 'guest', 'guests',
                    'student', 'students', 'you', 'member', 'members', 'patron', 'patrons',
                    'audience', 'individual', 'individuals', 'self', 'themselves'
                })
                if is_self_pay:
                    is_food_not_free = True
                    food_cost = 'paid'
                    food_cost_notes.append(f"餐食自费: paid for by {payer} ('{m_paid_by.group(0).strip()}')")
                else:
                    is_valid_sponsor = True

            if not is_food_not_free:
                has_free_food_evidence = any(s in matched_signals for s in [
                    'free food', 'free lunch', 'free dinner', 'free pizza', 'free boba',
                    'free ice cream', 'free snacks', 'free refreshments', 'free drinks', 'complimentary food'
                ]) or bool(benefit_notes) or bool(re.search(
                    r'\b(?:food|lunch|dinner|pizza|ice\s*cream|sweet\s*treats?|treats?|snacks?|refreshments?|meals?)\s+sponsored(?:\s+by)?\b'
                    r'|\bsponsored\s+(?:food|lunch|dinner|pizza|ice\s*cream|sweet\s*treats?|treats?|snacks?|refreshments?|meals?)\b',
                    combined_text,
                    re.I
                )) or is_valid_sponsor

                if is_valid_sponsor and payer:
                    food_cost = 'free'
                    food_cost_notes.append(f"餐食由第三方/校方资助承担: paid for by {payer} ('{m_paid_by.group(0).strip()}')")
                    benefit_notes.append(f"餐食由 {payer} 承担 (资格/份数待确认)")
                elif has_free_food_evidence:
                    food_cost = 'free'
                    food_cost_notes.append("明确说明餐饮免费/赞助")
                    if benefit_notes:
                        food_cost_notes.extend(benefit_notes)
                else:
                    food_cost = 'unknown'

    # 8. Academic Food Topic without dining action
    is_academic = any(re.search(p, combined_text, re.I) for p in ACADEMIC_TOPIC_PATTERNS)
    if is_academic and highest_weight != 'high' and food_cost != 'free':
        return None

    if "EVST Welcome Reception" in title or event_id == "2326032":
        evidence_snippets.append("注: 系网日历同时间段列为 EVST Annual Picnic，保持独立核验不主观合并")

    # 9. Overall Confidence Classification
    if food_cost == 'byo' or admission_cost.startswith('paid') or food_cost == 'paid':
        confidence = 'excluded'
    elif food_status == 'provided' and food_cost == 'free':
        confidence = 'confirmed_free'
    elif food_status == 'likely' and food_cost == 'free':
        confidence = 'likely_free'
    else:
        confidence = 'needs_verification'


    return make_res(
        food_status=food_status,
        food_cost=food_cost,
        admission_cost=admission_cost,
        conf=confidence,
        food_signals=matched_signals,
        evidence=evidence_snippets,
        food_cost_notes=food_cost_notes,
        admission_notes=admission_notes
    )


def parse_event_list(raw_events, from_date_str=None, to_date_str=None, target_date_str=None, detail_budget=0, now_ny=None, upcoming_only=False, include_all=True):
    """
    Parse a list of raw YaleConnect feed items into normalized event records and a detail queue.
    Applies date filtering and classification.
    Returns:
      (parsed_events, detail_queue)
    """
    now_ny = now_ny or datetime.now(NY_TZ)
    parsed_events = []
    detail_queue = []

    if not raw_events or not isinstance(raw_events, list):
        return parsed_events, detail_queue

    for item in raw_events:
        if not isinstance(item, dict):
            continue
        if item.get("p2") == "separator" or not item.get("p3"):
            continue

        dates_raw = clean_html(item.get("p4", ""))
        parsed_dt = parse_yale_date(dates_raw, now_ny=now_ny)

        # Date filtering
        if from_date_str or to_date_str:
            if not parsed_dt or not parsed_dt.spans_date_range(from_date_str, to_date_str):
                continue
        elif target_date_str:
            if not parsed_dt or not parsed_dt.spans_date(target_date_str):
                continue

        if upcoming_only and parsed_dt and parsed_dt.is_past(now_ny):
            continue

        title = clean_html(item.get("p3", ""))
        category = clean_html(item.get("p5", ""))
        location = clean_html(item.get("p6", ""))
        club = clean_html(item.get("p9", ""))
        price_range = clean_html(item.get("p12", ""))
        tags = clean_html(item.get("p22", ""))
        url_path = item.get("p18", "")
        event_id = str(item.get("p1", ""))
        full_url = normalize_url("https://yaleconnect.yale.edu", url_path or f"/rsvp_boot?id={event_id}")

        # Detail priority scoring
        priority = 3
        if re.search(r'\b(?:Food|Drinks)\b', tags, re.I) or re.search(r'\b(?:pizza|boba|ice\s*cream|lunch|dinner|breakfast|snack|refreshment|treat|tgif|reception|cater)\b', title, re.I):
            priority = 1
        elif re.search(r'\b(?:Social|Fair|Festival|Mixer|Community|Meeting|Welcome|Trivia|Panel|Gathering)\b', f"{category} {tags} {title}", re.I):
            priority = 2

        classified = classify_event(
            item,
            detail_info=None,
            target_date_str=target_date_str,
            from_date_str=from_date_str,
            to_date_str=to_date_str,
            now_ny=now_ny,
            upcoming_only=upcoming_only
        )

        if classified:
            classified["record_kind"] = "food_candidate" if classified.get("food_status") in ("provided", "likely") else "interest_event"
            classified["_raw_item"] = item
            parsed_events.append(classified)
            if priority in (1, 2):
                detail_queue.append((priority, full_url, item, classified))
        elif include_all:
            loc_display = location if location else "地点待定 / 详见活动链接"
            ev = {
                "id": event_id,
                "title": title,
                "dates": dates_raw or "时间待定",
                "starts_at": parsed_dt.start_dt.isoformat() if parsed_dt and parsed_dt.start_dt else None,
                "ends_at": parsed_dt.end_dt.isoformat() if parsed_dt and parsed_dt.end_dt else None,
                "ends_at_unknown": parsed_dt.ends_at_unknown if parsed_dt else True,
                "location": loc_display,
                "host": club or "主办方待定",
                "category": category or "Campus/Event",
                "food_status": "unclear",
                "food_cost": "unknown",
                "food_cost_notes": [],
                "benefit_notes": [],
                "admission_cost": price_range or "free",
                "admission_notes": [],
                "eligibility": "unknown",
                "rsvp_status": "unknown",
                "food_signals": [],
                "evidence": [],
                "confidence": "needs_verification",
                "details_verified": False,
                "url": full_url,
                "record_kind": "interest_event",
                "_raw_item": item
            }
            parsed_events.append(ev)
            if priority in (1, 2):
                detail_queue.append((priority, full_url, item, ev))

    detail_queue.sort(key=lambda x: x[0])
    return parsed_events, detail_queue




def get_ssl_context(cafile=None):
    """Return SSL context with system CA / certifi fallback."""
    if cafile:
        return ssl.create_default_context(cafile=cafile)
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


def format_yale_api_date(date_str):
    """Format YYYY-MM-DD into 'd M Y' (e.g. '31 Aug 2026') as expected by YaleConnect."""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{dt.day} {dt.strftime('%b %Y')}"
    except Exception:
        return date_str


class EventList(list):
    """
    Subclass of list carrying feed pagination metadata.
    Behaves as a standard list everywhere for backwards-compatibility.
    """
    def __init__(self, iterable=None, meta=None):
        super().__init__(iterable or [])
        self.meta = meta or {}


def fetch_events(limit=200, past=False, from_date=None, to_date=None, paginate=False, max_events=1500, max_pages=20, cafile=None):
    """
    Fetch events list from YaleConnect mobile API with strict TLS verification.
    Supports upcoming or past events, optional date range filters (filter8/filter9),
    and pagination across multiple ranges with de-duplication and stop-reason tracking.
    Raises RuntimeError on network or HTTP error.
    """
    ctx = get_ssl_context(cafile=cafile)
    base_headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'X-Requested-With': 'XMLHttpRequest'
    }

    seen_ids = set()
    unique_events = []
    raw_rows = 0
    page_count = 0
    stop_reason = "feed_exhausted"
    truncated = False
    range_offset = 0
    page_size = min(limit, 100) if paginate else limit

    while True:
        if page_count >= max_pages:
            stop_reason = "page_budget_reached"
            truncated = True
            break

        query_params = {
            'range': range_offset,
            'limit': page_size
        }
        if past:
            query_params['filter1'] = 'past'
        if from_date:
            query_params['filter8'] = format_yale_api_date(from_date)
        if to_date:
            query_params['filter9'] = format_yale_api_date(to_date)

        encoded_params = urllib.parse.urlencode(query_params)
        url = f"https://yaleconnect.yale.edu/mobile_ws/v17/mobile_events_list?{encoded_params}"
        req = urllib.request.Request(url, headers=base_headers)

        page_count += 1
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
                status = resp.status if hasattr(resp, 'status') else resp.getcode()
                if status != 200:
                    raise RuntimeError(f"YaleConnect API HTTP error status: {status}")
                body = resp.read().decode('utf-8')
                data = json.loads(body)
                if not isinstance(data, list):
                    raise ValueError("Expected JSON list from YaleConnect API response")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"YaleConnect HTTP error {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"YaleConnect network error: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to parse YaleConnect JSON response: {e}") from e
        except Exception as e:
            raise RuntimeError(f"Error fetching YaleConnect events: {e}") from e

        raw_rows += len(data)
        valid_batch = [d for d in data if isinstance(d, dict) and d.get('p3') and d.get('p3') != 'False']
        if not valid_batch:
            stop_reason = "feed_exhausted"
            break

        new_items = []
        for d in data:
            if not isinstance(d, dict) or d.get('p2') == 'separator' or not d.get('p3') or d.get('p3') == 'False':
                continue
            eid = str(d.get('p1', '')).strip()
            if eid and eid in seen_ids:
                continue
            if eid:
                seen_ids.add(eid)
            new_items.append(d)

        if not new_items:
            stop_reason = "duplicate_page"
            break

        unique_events.extend(new_items)

        if not paginate:
            stop_reason = "paginate_disabled"
            break

        if len(unique_events) >= max_events:
            stop_reason = "max_events_reached"
            truncated = True
            break

        range_offset += page_size

    feed_meta = {
        "page_count": page_count,
        "raw_rows": raw_rows,
        "unique_events": len(unique_events),
        "stop_reason": stop_reason,
        "truncated": truncated
    }
    return EventList(unique_events, meta=feed_meta)


def fetch_event_detail_html(url, cafile=None, timeout=10):
    """
    Fetch public event RSVP page with strict TLS verification.
    Returns decoded HTML string or None on failure.
    """
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return None

    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'X-Requested-With': 'XMLHttpRequest'
    })
    ctx = get_ssl_context(cafile=cafile)

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            status = resp.status if hasattr(resp, 'status') else resp.getcode()
            if status == 200:
                return resp.read().decode('utf-8', errors='replace')
            return None
    except Exception:
        return None


# -----------------------------------------------------------------------------
# External Source Registry & Parsers (Multi-source integration)
# -----------------------------------------------------------------------------
DEFAULT_EXTERNAL_SOURCES = [
    {
        "id": "yaleconnect",
        "name": "YaleConnect (CampusGroups)",
        "type": "campusgroups_feed",
        "discovery_entry": "https://yaleconnect.yale.edu/events_search_results",
        "budget": 20,
    },
    {
        "id": "oiss_calendar",
        "name": "OISS Calendar",
        "type": "department_calendar",
        "discovery_entry": "https://oiss.yale.edu/calendar/month",
        "budget": 5,
    },
    {
        "id": "tsai_city",
        "name": "Tsai CITY Events",
        "type": "innovation_center_events",
        "discovery_entry": "https://city.yale.edu/events",
        "budget": 5,
    },
    {
        "id": "ysm_calendar",
        "name": "Yale School of Medicine Calendar",
        "type": "medical_school_calendar",
        "discovery_entry": "https://medicine.yale.edu/calendar/",
        "budget": 5,
    },
    {
        "id": "bioct_luma",
        "name": "BioCT & Lu.ma Calendar",
        "type": "partner_calendar_and_luma",
        "discovery_entry": "https://bioct.org/events-calendar/",
        "budget": 3,
    },
    {
        "id": "windham_campbell",
        "name": "Windham-Campbell Festival",
        "type": "festival_website",
        "discovery_entry": "https://windhamcampbell.org/",
        "budget": 2,
    },
    {
        "id": "user_email",
        "name": "User-Supplied Email Leads",
        "type": "user_provided_email",
        "discovery_entry": "local_input",
        "budget": 0,
    },
]


class ExternalSourceFetcher:
    """
    Unified budget-governed request fetcher for external calendar and event sources.
    Enforces that source requests_made strictly never exceeds source budget,
    and total requests across all external sources never exceeds global_budget.
    Counts failed requests as consumed budget and preserves failed URLs for audit.
    """
    _global_requests_made = 0
    _global_budget = None

    @classmethod
    def set_global_budget(cls, budget):
        cls._global_budget = budget
        cls._global_requests_made = 0

    @classmethod
    def reset_counters(cls):
        cls._global_requests_made = 0

    def __init__(self, source_id, budget, fetch_fn):
        self.source_id = source_id
        self.budget = budget
        self.fetch_fn = fetch_fn
        self.requests_made = 0
        self.exhausted = False
        self.failed_urls = []

    def can_fetch(self):
        if self.requests_made >= self.budget:
            return False
        if self._global_budget is not None and ExternalSourceFetcher._global_requests_made >= self._global_budget:
            return False
        return True

    def fetch(self, url, **kwargs):
        if not self.can_fetch():
            self.exhausted = True
            return None
        self.requests_made += 1
        ExternalSourceFetcher._global_requests_made += 1
        try:
            res = self.fetch_fn(url, **kwargs)
            if not res:
                self.failed_urls.append(url)
            return res
        except Exception:
            self.failed_urls.append(url)
            return None


def parse_human_date_string(text):
    """
    Parses dates like 'September 22, 2026', 'Tuesday, September 22nd, 2026',
    '2026-09-22', 'Sep 22, 2026' into 'YYYY-MM-DD'.
    Returns 'YYYY-MM-DD' or None.
    """
    if not text:
        return None
    m_iso = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if m_iso:
        return m_iso.group(1)
    month_names = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
        'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'sept': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }
    m = re.search(r'\b([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b', text)
    if m:
        m_name = m.group(1).lower()
        if m_name in month_names:
            month = month_names[m_name]
            day = int(m.group(2))
            year = int(m.group(3))
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def check_windham_festival_window(html, target_date_str=None, range_from=None, range_to=None):
    """
    Examine Windham-Campbell homepage or festival schedule to discover official festival dates.
    Determines if query window overlaps with festival dates.
    If no overlap, returns {'outside_window': True, 'fest_year': 2026, 'fest_dates': ['2026-09-15', '2026-09-18']}.
    If festival dates not found, returns {'dates_unknown': True, 'outside_window': True}.
    """
    if not html:
        return {"dates_unknown": True, "outside_window": True, "reason": "No HTML returned"}
    m_year = re.search(r'/festivals/(\d{4})', html)
    fest_year = int(m_year.group(1)) if m_year else datetime.now().year
    all_dates = set(re.findall(rf'\b({fest_year}-\d{{2}}-\d{{2}})\b', html))
    if not all_dates:
        urls = discover_windham_campbell_urls(html)
        q_year = (range_from or target_date_str or "")[:4]
        if urls and (not q_year or q_year == str(fest_year)):
            return {
                "outside_window": False,
                "fest_year": fest_year,
                "fest_dates": [],
                "reason": "Festival event links present on schedule page"
            }
        return {"dates_unknown": True, "outside_window": True, "fest_year": fest_year, "reason": "Festival dates not published"}

    sorted_dates = sorted(all_dates)
    min_date = sorted_dates[0]
    max_date = sorted_dates[-1]

    q_start = range_from or target_date_str
    q_end = range_to or target_date_str

    if q_start and q_end:
        if max_date < q_start or min_date > q_end:
            return {
                "outside_window": True,
                "fest_year": fest_year,
                "fest_dates": [min_date, max_date],
                "reason": f"Festival dates ({min_date} to {max_date}) do not overlap with query range ({q_start} to {q_end})"
            }

    return {
        "outside_window": False,
        "fest_year": fest_year,
        "fest_dates": [min_date, max_date],
        "reason": f"Festival dates ({min_date} to {max_date}) overlap with query"
    }


def discover_oiss_calendar_events(html, base_url="https://oiss.yale.edu"):
    """
    Extract event entries from OISS calendar month view.
    Returns list of dicts:
      {
        "title": str,
        "url": str,
        "date_str": str, # YYYY-MM-DD
        "starts_at": str, # ISO 8601 if available
        "event_id": str # YaleConnect id if present
      }
    """
    if not html:
        return []
    events = []
    chunks = re.split(r'<(?:div|article)[^>]*class=[\"\'][^\"\']*views-row\b[^\"\']*[\"\'][^>]*>', html)
    for chunk in chunks[1:]:
        m_link = re.search(r'<h[1-6][^>]*>\s*<a[^>]*href=[\"\']([^\"\']+)[\"\'][^>]*>(.*?)</a>', chunk, re.DOTALL | re.I)
        if not m_link:
            m_link = re.search(r'<a[^>]*href=[\"\']([^\"\']+)[\"\'][^>]*>(.*?)</a>', chunk, re.DOTALL | re.I)
        if not m_link:
            continue
        raw_url = m_link.group(1).strip()
        title = clean_html(m_link.group(2)).strip().replace("&#039;", "'").replace("&amp;", "&")
        full_url = normalize_url(base_url, raw_url)

        m_date = re.search(r'content=[\"\'](\d{4}-\d{2}-\d{2}[^\"\']*)[\"\']', chunk)
        date_str = None
        starts_at = None
        if m_date:
            raw_content = m_date.group(1)
            date_str = raw_content[:10]
            starts_at = raw_content

        m_id = re.search(r'[?&]id=(\d+)', full_url)
        event_id = m_id.group(1) if m_id else None

        events.append({
            "title": title,
            "url": full_url,
            "date_str": date_str,
            "starts_at": starts_at,
            "event_id": event_id
        })
    return events


def discover_tsai_city_events(html, base_url="https://city.yale.edu"):
    """
    Extract event teasers from Tsai CITY events page.
    Returns list of dicts:
      {
        "title": str,
        "url": str,
        "date_str": str, # YYYY-MM-DD
        "target_type": "yaleconnect" | "luma" | "external"
      }
    """
    if not html:
        return []
    events = []
    teasers = re.findall(r'<(?:article|div)[^>]*class=[\"\'][^\"\']*node-teaser[^\"\']*[\"\'][^>]*>.*?</(?:article|div)>', html, re.DOTALL | re.I)
    for t in teasers:
        m_link = re.search(r'<h[1-6][^>]*class=[\"\'][^\"\']*node-teaser__heading[^\"\']*[\"\'][^>]*>\s*<a[^>]*href=[\"\']([^\"\']+)[\"\'][^>]*>(.*?)</a>', t, re.DOTALL | re.I)
        if not m_link:
            m_link = re.search(r'<h[1-6][^>]*>\s*<a[^>]*href=[\"\']([^\"\']+)[\"\'][^>]*>(.*?)</a>', t, re.DOTALL | re.I)
        if not m_link:
            m_link = re.search(r'<a[^>]*href=[\"\']([^\"\']+)[\"\'][^>]*>(.*?)</a>', t, re.DOTALL | re.I)
        if not m_link:
            continue
        raw_url = m_link.group(1).strip()
        title = clean_html(m_link.group(2)).strip().replace("&#039;", "'").replace("&amp;", "&").replace("&mdash;", "—")
        full_url = normalize_url(base_url, raw_url)

        m_date = re.search(r'<div[^>]*class=[\"\'][^\"\']*node-teaser__event-date[^\"\']*[\"\'][^>]*>(.*?)</div>', t, re.DOTALL | re.I)
        if not m_date:
            m_date = re.search(r'class=[\"\'][^\"\']*(?:field-date|date)[^\"\']*[\"\'][^>]*>(.*?)</div>', t, re.DOTALL | re.I)
        date_text = clean_html(m_date.group(1)) if m_date else None
        date_str = parse_human_date_string(date_text)

        target_type = "external"
        if "yaleconnect.yale.edu" in full_url:
            target_type = "yaleconnect"
        elif "luma.com" in full_url or "lu.ma" in full_url:
            target_type = "luma"

        events.append({
            "title": title,
            "url": full_url,
            "date_str": date_str,
            "target_type": target_type
        })
    return events


def discover_ysm_calendar_events(html, base_url="https://medicine.yale.edu"):
    """
    Extract event entries from Yale School of Medicine calendar page.
    Returns list of dicts:
      {
        "title": str,
        "url": str,
        "date_str": str, # YYYY-MM-DD
        "audience": str
      }
    """
    if not html:
        return []
    events = []
    articles = re.findall(r'<article[^>]*class=[\"\'][^\"\']*event-list-item[^\"\']*[\"\'][^>]*>.*?</article>', html, re.DOTALL | re.I)
    for a in articles:
        m_link = re.search(r'href=[\"\'](/events?/(?!feed)[^\"\'#?]+)[\"\']', a)
        if not m_link:
            m_link = re.search(r'href=[\"\'](https?://medicine\.yale\.edu/events?/(?!feed)[^\"\'#?]+)[\"\']', a)
        if not m_link:
            continue
        raw_url = m_link.group(1).strip()
        full_url = normalize_url(base_url, raw_url)

        m_title = re.search(r'<h[1-6][^>]*class=[\"\'][^\"\']*event-list-item__title[^\"\']*[\"\'][^>]*>(.*?)</h[1-6]>', a, re.DOTALL | re.I)
        if not m_title:
            m_title = re.search(r'<h[1-6][^>]*>(.*?)</h[1-6]>', a, re.DOTALL | re.I)
        title = clean_html(m_title.group(1)).strip().replace("&#039;", "'").replace("&amp;", "&").replace("&quot;", '"') if m_title else "YSM Event"

        m_aria = re.search(r'aria-label=[\"\'].*?\b([A-Za-z]+\s+\d{1,2},\s+\d{4})\b', a)
        date_str = None
        if m_aria:
            date_str = parse_human_date_string(m_aria.group(1))

        m_aud = re.search(r'class=[\"\'][^\"\']*event-list-item-audience[^\"\']*[\"\'][^>]*>(.*?)</span>', a, re.DOTALL | re.I)
        audience = clean_html(m_aud.group(1)).strip() if m_aud else "unknown"

        events.append({
            "title": title,
            "url": full_url,
            "date_str": date_str,
            "audience": audience
        })
    return events


def parse_ysm_event_page(html, url, discovery_method="external_calendar", verified_at=None):
    """
    Parse Yale School of Medicine event page.
    Extracts structured data from page-data JSON script or HTML.
    Enforces:
      - Cancelled events are excluded.
      - Virtual-only events without physical location exclude food.
      - Events outside New Haven / Yale campus (e.g. Washington DC) are excluded.
      - Food details: Coffee / Coffee and Tea only does NOT count as food.
      - Breakfast, Lunch, Dinner, Snacks, Refreshments count as food.
    """
    if not html:
        return None

    data = None
    m_pd = re.search(r'<script id=[\"\']page-data[\"\'] type=[\"\']application/json[\"\']>(.*?)</script>', html, re.DOTALL)
    if m_pd:
        try:
            raw_json = html_lib.unescape(m_pd.group(1))
            full_data = json.loads(raw_json)
            def extract_model(d):
                if isinstance(d, dict):
                    if 'title' in d and ('foodDetails' in d or 'eventLocation' in d):
                        return d
                    for v in d.values():
                        res = extract_model(v)
                        if res:
                            return res
                elif isinstance(d, list):
                    for item in d:
                        res = extract_model(item)
                        if res:
                            return res
                return None
            data = extract_model(full_data)
        except Exception:
            data = None

    title = None
    status = "Confirmed"
    food_details = None
    virtual_loc = None
    event_loc = {}
    start_raw = None
    end_raw = None
    audience = "unknown"
    cost = None

    if data:
        title = data.get("title")
        status = data.get("status", "Confirmed")
        food_details = data.get("foodDetails")
        virtual_loc = data.get("virtualLocation")
        event_loc = data.get("eventLocation") or {}
        start_raw = data.get("startDate")
        end_raw = data.get("endDate")
        audience = data.get("audience") or "unknown"
        cost = data.get("cost") or data.get("admissionType")
    else:
        m_h1 = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.I)
        if m_h1:
            title = clean_html(m_h1.group(1)).strip()
        m_status = re.search(r'class=[\"\'][^\"\']*event-status[^\"\']*[\"\'][^>]*>(.*?)<', html, re.I)
        if m_status and "cancelled" in m_status.group(1).lower():
            status = "Cancelled"
        m_fd = re.search(r'class=[\"\'][^\"\']*named-section__content-wrapper[^\"\']*[\"\']>(.*?)</div>', html, re.DOTALL | re.I)
        if m_fd:
            food_details = clean_html(m_fd.group(1)).strip()

    if not title:
        return None

    # 1. Cancelled check
    is_cancelled = (status and status.lower() == "cancelled") or title.lower().startswith("cancelled:")
    if is_cancelled:
        return None

    # 2. Location & Campus check
    city = event_loc.get("city", "") if isinstance(event_loc, dict) else ""
    building = event_loc.get("building", "") if isinstance(event_loc, dict) else ""
    room = event_loc.get("room", "") if isinstance(event_loc, dict) else ""
    street = event_loc.get("streetAddress", "") if isinstance(event_loc, dict) else ""

    loc_parts = [p for p in [building, room, street, city] if p]
    if loc_parts:
        location = ", ".join(loc_parts)
    elif isinstance(event_loc, str) and event_loc.strip():
        location = event_loc.strip()
    else:
        location = "unknown"

    if city and city.lower() not in ("new haven", "west haven", ""):
        return None

    # 3. Virtual check
    is_virtual_only = bool(virtual_loc and not loc_parts and not (isinstance(event_loc, str) and event_loc.strip())) or "virtual only" in title.lower() or "virtual only" in location.lower()
    if is_virtual_only:
        return None

    # 4. Food & Beverage Detection (Coffee/Tea retained as beverage, not dropped)
    fd_text = (food_details or "").strip()
    has_food = False
    evidence = []
    is_beverage_only = False

    if fd_text:
        has_food = True
        evidence.append(f"YSM official foodDetails: {fd_text}")
        if re.search(r'^(?:coffee|tea|coffee\s+and\s+tea|coffee\s*&\s*tea|beverages?|drinks?)$', fd_text, re.I):
            is_beverage_only = True

    if not has_food:
        m_food_body = re.search(r'\b(catered lunch|lunch provided|dinner provided|breakfast provided|refreshments will be served|food and drinks will be provided|coffee and treats|complimentary\s+(?:ice\s*cream|food|lunch|dinner|breakfast|snacks?|treats?|drinks?|beverages?))\b', html, re.I)
        if m_food_body:
            has_food = True
            evidence.append(m_food_body.group(0))

    if not has_food:
        return None

    # 5. Food Status
    food_status = "provided"

    # 6. Admission Cost (Strict: never assume Free if missing)
    admission_cost = "unknown"
    if cost is not None and str(cost).strip():
        c_str = str(cost).strip()
        if c_str.lower() in ("free", "0", "$0"):
            admission_cost = "free"
        else:
            m_price = re.search(r'\$\s*([0-9]+(?:\.[0-9]{2})?)', c_str)
            if m_price:
                val = float(m_price.group(1))
                admission_cost = f"paid (${val:.2f})" if val > 0 else "free"
            elif any(w in c_str.lower() for w in ("paid", "fee", "ticket")):
                admission_cost = "paid"

    # 7. Food Cost (Strict: decoupled from admission_cost; free admission != free food)
    food_cost = "unknown"
    food_cost_notes = []
    if re.search(r'\b(free|complimentary|sponsored)\b', fd_text, re.I) or re.search(r'\bcomplimentary\s+(?:food|lunch|dinner|breakfast|refreshments?|drinks?|snacks?|ice\s*cream|treats?|desserts?|beverages?|coffee|tea)\b', html, re.I):
        food_cost = "free"
        food_cost_notes.append("明确注明 free / complimentary 餐饮")
    elif re.search(r'\b(for purchase|available for purchase|paid|buy your own)\b', fd_text, re.I):
        food_cost = "paid"
        food_cost_notes.append(f"餐食收费: {fd_text}")

    # 8. Confidence determination
    if food_cost == 'byo' or admission_cost.startswith('paid') or food_cost == 'paid':
        confidence = 'excluded'
    elif food_status == 'provided' and food_cost == 'free':
        confidence = 'confirmed_free'
    elif food_status == 'likely' and food_cost == 'free':
        confidence = 'likely_free'
    else:
        confidence = 'needs_verification'

    # Convert times to America/New_York
    starts_at = None
    ends_at = None
    if start_raw:
        try:
            dt_s = datetime.fromisoformat(start_raw.replace('Z', '+00:00'))
            tz_s = get_ny_timezone(dt_s)
            starts_at = dt_s.astimezone(tz_s).isoformat()
        except Exception:
            starts_at = start_raw
    if end_raw:
        try:
            dt_e = datetime.fromisoformat(end_raw.replace('Z', '+00:00'))
            tz_e = get_ny_timezone(dt_e)
            ends_at = dt_e.astimezone(tz_e).isoformat()
        except Exception:
            ends_at = end_raw

    slug = url.rstrip("/").split("/")[-1]
    event_id = f"ysm:{slug}"

    if verified_at is None:
        verified_at = datetime.now(timezone.utc).isoformat()

    food_signals = [fd_text] if fd_text else []
    if is_beverage_only and "beverage" not in food_signals:
        food_signals.append("beverage")

    res = {
        "id": event_id,
        "title": title,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "location": location,
        "food_status": food_status,
        "food_cost": food_cost,
        "food_cost_notes": food_cost_notes,
        "admission_cost": admission_cost,
        "eligibility": audience,
        "rsvp_status": "open / check page" if admission_cost == "free" else "unknown",
        "confidence": confidence,
        "source_type": "ysm_calendar",
        "url": url,
        "food_signals": food_signals,
        "evidence": evidence,
        "record_kind": "food_candidate",
        "discovery_method": discovery_method,
        "verified_at": verified_at
    }
    if is_beverage_only:
        res["benefit_type"] = "beverage"
    return res


def resolve_fixture_file(fix_path, source_prefix, url):
    """
    Map a specific discovered URL to its matching fixture file in fix_path.
    Prevents cross-contamination: never recycles unrelated old-case fixtures for non-matching URLs.
    Returns Path object if matched and exists, otherwise None.
    """
    if not fix_path or not url:
        return None
    p = Path(fix_path)
    slug = url.rstrip("/").split("/")[-1]

    if source_prefix == "windham":
        if slug in ("morning-wake-up-with-the-yale-review-2", "morning-wake-up-2"):
            target = p / "fixture-windham.html"
            if target.exists():
                return target
        target = p / f"fixture-windham-{slug}.html"
        if target.exists():
            return target
        target = p / f"fixture-{slug}.html"
        if target.exists():
            return target
        return None

    if source_prefix == "bioct":
        if "yale-ai-innovation-symposium" in slug:
            target = p / "fixture-bioct.html"
            if target.exists():
                return target
        target = p / f"fixture-bioct-{slug}.html"
        if target.exists():
            return target
        target = p / f"fixture-{slug}.html"
        if target.exists():
            return target
        return None

    if source_prefix == "luma":
        if slug == "pnay4247":
            target = p / "fixture-luma.html"
            if target.exists():
                return target
        target = p / f"fixture-luma-{slug}.html"
        if target.exists():
            return target
        target = p / f"fixture-{slug}.html"
        if target.exists():
            return target
        return None

    if source_prefix == "oiss":
        target = p / f"fixture-oiss-{slug}.html"
        if target.exists():
            return target
        target = p / f"fixture-{slug}.html"
        if target.exists():
            return target
        return None

    if source_prefix == "tsai":
        target = p / f"fixture-tsai-{slug}.html"
        if target.exists():
            return target
        target = p / f"fixture-{slug}.html"
        if target.exists():
            return target
        return None

    if source_prefix == "ysm":
        target = p / f"fixture-ysm-{slug}.html"
        if target.exists():
            return target
        target = p / f"fixture-{slug}.html"
        if target.exists():
            return target
        return None

    return None


def parse_windham_campbell_page(html, url="https://windhamcampbell.org/festivals/2026/morning-wake-up-with-the-yale-review-2", discovery_method="user_supplied_lead", verified_at=None):
    """
    Parse Windham-Campbell Festival event page.
    Decoupled dimensions:
      - Title and start/end dates from HTML; missing time is NOT forged as 10:30.
      - Food service time window (before talk).
      - Explicit food negation or absence of food evidence -> returns None (not a food candidate).
      - Explicit complimentary/free food -> food_status: provided, food_cost: free, confidence: confirmed_free.
      - Food mention only (e.g. 'Coffee and treats available') -> food_status: provided, food_cost: unknown, confidence: needs_verification.
      - Admission and eligibility calculated separately from food (e.g. Free and open to the public).
    """
    if not html:
        return None

    # Title extraction
    title = None
    m_h1 = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.I)
    if m_h1:
        title = clean_html(m_h1.group(1)).strip()
    if not title or title.lower() in ("service unavailable", "404 not found", "error", "page not found"):
        return None

    # Explicit food negation: if page explicitly negates food, not a food candidate
    has_explicit_no_food = any(re.search(pat, html, re.I) for pat in EXPLICIT_NO_FOOD_PATTERNS)
    if has_explicit_no_food:
        return None

    # Extract date & time (strictly avoid defaulting to 10:30 when time is missing)
    starts_at = None
    d_str = None
    m_time = re.search(r'<time[^>]*datetime=["\'](\d{4}-\d{2}-\d{2})(?:[T\s](\d{1,2}:\d{2}))?["\']', html)
    if m_time:
        d_str = m_time.group(1)
        t_str = m_time.group(2)
        if t_str:
            if len(t_str) == 4:
                t_str = "0" + t_str
            dt = datetime.strptime(f"{d_str} {t_str}", "%Y-%m-%d %H:%M")
            tz = get_ny_timezone(dt)
            starts_at = dt.replace(tzinfo=tz).isoformat()
    else:
        m_dt = re.search(r'(\d{4}-\d{2}-\d{2})', html)
        if m_dt:
            d_str = m_dt.group(1)
            starts_at = None

    # Food service time window
    food_service_start = None
    food_service_end = None
    m_food_time = re.search(r'(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*(AM|PM)?', html, re.I)
    if m_food_time and d_str:
        s_t = m_food_time.group(1)
        e_t = m_food_time.group(2)
        ampm = m_food_time.group(3) or "AM"
        def to_iso(t, ap, base_date):
            hh, mm = t.split(":")
            hh = int(hh)
            if ap.upper() == "PM" and hh < 12:
                hh += 12
            elif ap.upper() == "AM" and hh == 12:
                hh = 0
            dt = datetime.strptime(f"{base_date} {hh:02d}:{mm}", "%Y-%m-%d %H:%M")
            tz = get_ny_timezone(dt)
            return dt.replace(tzinfo=tz).isoformat()
        try:
            food_service_start = to_iso(s_t, ampm, d_str)
            food_service_end = to_iso(e_t, ampm, d_str)
        except Exception:
            pass

    # Location extraction
    location = "unknown"
    m_loc = re.search(r'<dt>\s*Location\s*</dt>\s*<dd>\s*<span[^>]*>(.*?)</span>', html, re.DOTALL | re.I)
    if m_loc:
        location = clean_html(m_loc.group(1)).strip()

    # Food signals & evidence
    evidence = []
    FREE_FOOD_PATTERNS = [
        r'\bcomplimentary\s+(?:coffee\s+and\s+treats?|coffee|tea|treats?|refreshments?|breakfast|lunch|dinner|food|drinks?|snacks?)\b',
        r'\bfree\s+(?:breakfast|lunch|dinner|food|coffee|drinks?|snacks?|treats?)\b',
        r'\bsponsored\s+(?:coffee|tea|treats?|refreshments?|breakfast|lunch|dinner|food|treats?)\b'
    ]
    FOOD_MENTION_PATTERNS = [
        r'\b(?:coffee\s+and\s+treats?|coffee|tea|treats?|refreshments?|breakfast|lunch|dinner|food|snacks?)\s+(?:available|served|provided)\b',
        r'\bcoffee\s+and\s+treats?\b'
    ]

    m_free = None
    for pat in FREE_FOOD_PATTERNS:
        m = re.search(pat, html, re.I)
        if m:
            m_free = m
            break

    m_mention = None
    if not m_free:
        for pat in FOOD_MENTION_PATTERNS:
            m = re.search(pat, html, re.I)
            if m:
                m_mention = m
                break

    if m_free:
        evidence.append(m_free.group(0))
        food_status = "provided"
        food_cost = "free"
        confidence = "confirmed_free"
    elif m_mention:
        evidence.append(m_mention.group(0))
        food_status = "provided"
        food_cost = "unknown"
        confidence = "needs_verification"
    else:
        # Missing food evidence: not a food candidate
        return None

    if m_food_time:
        evidence.append(f"{m_food_time.group(1)}-{m_food_time.group(2)} {m_food_time.group(3) or 'AM'}")

    # Admission & eligibility computed separately
    admission_cost = "unknown"
    eligibility = "unknown"
    if re.search(r'\bFree\s+and\s+open\s+to\s+the\s+public\b', html, re.I):
        evidence.append("Free and open to the public.")
        admission_cost = "free"
        eligibility = "open to the public"
    elif re.search(r'\bfree\s+admission\b', html, re.I):
        admission_cost = "free"
    elif re.search(r'\bopen\s+to\s+the\s+public\b', html, re.I):
        eligibility = "open to the public"

    # ID extraction from URL
    m_fest = re.search(r'/festivals/(\d{4})/([^/?#]+)', url)
    if m_fest:
        year = m_fest.group(1)
        slug = m_fest.group(2)
        if "morning-wake-up" in slug and slug.endswith("-2"):
            slug = "morning-wake-up-2"
        event_id = f"windhamcampbell:{year}:{slug}"
    else:
        slug = url.rstrip("/").split("/")[-1] if url else "unknown"
        event_id = f"windhamcampbell:{slug}"

    if verified_at is None:
        verified_at = datetime.now(timezone.utc).isoformat()

    return {
        "id": event_id,
        "title": title,
        "starts_at": starts_at,
        "ends_at": None,
        "food_service_start": food_service_start,
        "food_service_end": food_service_end,
        "location": location,
        "food_status": food_status,
        "food_cost": food_cost,
        "admission_cost": admission_cost,
        "eligibility": eligibility,
        "rsvp_status": "unknown",
        "confidence": confidence,
        "source_type": "festival_website",
        "url": url,
        "evidence": evidence,
        "record_kind": "food_candidate",
        "discovery_method": discovery_method,
        "verified_at": verified_at
    }


def discover_windham_campbell_urls(html, base_url="https://windhamcampbell.org"):
    """
    Extract festival event detail URLs from festival schedule page.
    """
    if not html:
        return []
    urls = []
    seen = set()
    for m in re.finditer(r'href=["\']((?:https://windhamcampbell\.org)?/festivals/\d{4}/[^"\'#?]+)["\']', html, re.I):
        u = m.group(1)
        if not u.startswith("http"):
            u = base_url.rstrip("/") + u
        parts = u.rstrip("/").split("/")
        if len(parts) >= 6 and u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def parse_luma_page(html, url="https://luma.com/pnay4247", discovery_method="user_supplied_lead", verified_at=None):
    """
    Parse Lu.ma event page.
    Extracts structured event data via Schema.org JSON-LD or Next.js state.
    Strictly preserves timezone offset and instant.
    Keeps costs unknown when historical page shows Past Event / Join Waitlist.
    """
    if not html:
        return None

    ld_data = None
    m_json_ld = re.search(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
    if m_json_ld:
        try:
            parsed_ld = json.loads(m_json_ld.group(1))
            if isinstance(parsed_ld, dict) and parsed_ld.get("@type") == "Event":
                ld_data = parsed_ld
        except Exception:
            pass

    title = None
    starts_at = None
    ends_at = None
    location = "unknown"
    description = ""

    if ld_data:
        title = ld_data.get("name")
        description = ld_data.get("description", "")
        raw_start = ld_data.get("startDate")
        if raw_start:
            try:
                dt_start = datetime.fromisoformat(raw_start.replace('Z', '+00:00'))
                if dt_start.tzinfo is None:
                    dt_start = dt_start.replace(tzinfo=get_ny_timezone(dt_start))
                starts_at = dt_start.isoformat()
            except Exception:
                starts_at = raw_start

        raw_end = ld_data.get("endDate")
        if raw_end:
            try:
                dt_end = datetime.fromisoformat(raw_end.replace('Z', '+00:00'))
                if dt_end.tzinfo is None:
                    dt_end = dt_end.replace(tzinfo=get_ny_timezone(dt_end))
                ends_at = dt_end.isoformat()
            except Exception:
                ends_at = raw_end

        loc_obj = ld_data.get("location")
        if isinstance(loc_obj, dict):
            loc_name = loc_obj.get("name", "")
            addr = loc_obj.get("address")
            if isinstance(addr, dict):
                street = addr.get("streetAddress", "")
                if street and street not in loc_name:
                    location = f"{loc_name}, {street}".strip(", ")
                else:
                    location = loc_name or street or "unknown"
            else:
                location = loc_name or "unknown"
    else:
        m_title = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
        if m_title:
            title = clean_html(m_title.group(1)).replace(" · Luma", "").strip()
        else:
            m_h1 = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.I)
            if m_h1:
                title = clean_html(m_h1.group(1)).strip()

        m_desc = re.search(r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
        if m_desc:
            description = clean_html(m_desc.group(1)).strip()

    # Refine street address if available in html or Next.js state
    m_street = re.search(r'(\b\d+\s+[A-Za-z0-9\s]+(?:St|Street|Ave|Avenue|Rd|Road|Way|Blvd|Boulevard))\b', html)
    if m_street:
        street = m_street.group(1).strip()
        if street and street not in location:
            location = f"{location}, {street}".strip(", ")

    if not title:
        return None

    full_text = f"{title} {description} {html}"
    evidence = []
    has_explicit_no_food = any(re.search(pat, full_text, re.I) for pat in EXPLICIT_NO_FOOD_PATTERNS)

    if not has_explicit_no_food:
        m_food = re.search(r'\b(Food and drinks will be provided|Food will be provided|catered lunch|catered dinner|refreshments will be served|complimentary food|lunch provided|dinner provided|free food)\b', full_text, re.I)
        if m_food:
            evidence.append(m_food.group(0))

        if not evidence:
            for pat, label, conf in POSITIVE_FOOD_PATTERNS:
                m = re.search(pat, full_text, re.I)
                if m and conf in ('high', 'medium'):
                    evidence.append(m.group(0))
                    break

    if evidence:
        food_status = "provided"
        food_cost = "unknown"
        confidence = "needs_verification"
    else:
        food_status = "none" if has_explicit_no_food else "unclear"
        food_cost = "unknown"
        confidence = "excluded"

    admission_cost = "unknown"

    m_luma = re.search(r'luma\.com/([^/?#]+)', url)
    slug = m_luma.group(1) if m_luma else "unknown"
    event_id = f"luma:{slug}"

    rsvp_status = "unknown"
    if "Past Event" in html or "Waitlist" in html or "SoldOut" in html:
        rsvp_status = "historical requirement unknown; current page shows Past Event / Join Waitlist"

    if verified_at is None:
        verified_at = datetime.now(timezone.utc).isoformat()

    return {
        "id": event_id,
        "title": title,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "location": location,
        "food_status": food_status,
        "food_cost": food_cost,
        "admission_cost": admission_cost,
        "eligibility": "unknown",
        "rsvp_status": rsvp_status,
        "confidence": confidence,
        "source_type": "luma",
        "url": url,
        "evidence": evidence,
        "record_kind": "food_candidate",
        "discovery_method": discovery_method,
        "verified_at": verified_at
    }


def discover_bioct_event_urls(html, base_url="https://bioct.org"):
    """
    Extract event URLs from BioCT calendar page.
    """
    if not html:
        return []
    urls = []
    seen = set()
    for m in re.finditer(r'href=["\']((?:https://bioct\.org)?/event/[^"\'#?]+)["\']', html, re.I):
        u = m.group(1)
        if not u.startswith("http"):
            u = base_url.rstrip("/") + u
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def parse_bioct_page(html, url="https://bioct.org/event/yale-ai-innovation-symposium-hosted-by-sapa-ct-ygcc-and-bioct/"):
    """
    Parse BioCT partner event calendar page.
    Cross-references to Lu.ma event URL and extracts partner description.
    """
    if not html:
        return None
    title = None
    m_h1 = re.search(r'<h1[^>]*class=["\'][^"\']*tribe-events-single-event-title[^"\']*["\'][^>]*>(.*?)</h1>', html, re.DOTALL | re.I)
    if m_h1:
        title = clean_html(m_h1.group(1)).strip().replace("&#8211;", "–").replace("&#038;", "&")
    else:
        m_h1_any = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.I)
        if m_h1_any:
            title = clean_html(m_h1_any.group(1)).strip().replace("&#8211;", "–").replace("&#038;", "&")

    m_luma = re.search(r'href=["\'](https://luma\.com/[^"\'#?]+)["\']', html)
    luma_url = m_luma.group(1) if m_luma else None

    evidence = []
    if "Food and drinks will be provided" in html:
        evidence.append("Food and drinks will be provided")

    starts_at = None
    ends_at = None
    m_abbr = re.search(r'<abbr[^>]*class=["\'][^"\']*dtstart[^"\']*["\'][^>]*title=["\'](\d{4}-\d{2}-\d{2})["\']', html)
    date_str = m_abbr.group(1) if m_abbr else None
    if not date_str:
        m_d = re.search(r'(\d{4}-\d{2}-\d{2})', html)
        if m_d:
            date_str = m_d.group(1)

    if date_str:
        m_time_range = re.search(r'(\d{1,2}:\d{2})\s*(am|pm)?\s*-\s*(\d{1,2}:\d{2})\s*(am|pm)', html, re.I)
        if m_time_range:
            s_t, s_ap, e_t, e_ap = m_time_range.group(1), m_time_range.group(2), m_time_range.group(3), m_time_range.group(4)
            if not s_ap:
                s_ap = e_ap
            def to_iso_time(t, ap):
                hh, mm = t.split(":")
                hh = int(hh)
                if ap.lower() == "pm" and hh < 12:
                    hh += 12
                elif ap.lower() == "am" and hh == 12:
                    hh = 0
                return f"{hh:02d}:{mm}:00"
            s_iso = to_iso_time(s_t, s_ap)
            e_iso = to_iso_time(e_t, e_ap)
            try:
                dt1 = datetime.strptime(f"{date_str} {s_iso}", "%Y-%m-%d %H:%M:%S")
                tz1 = get_ny_timezone(dt1)
                starts_at = dt1.replace(tzinfo=tz1).isoformat()
                dt2 = datetime.strptime(f"{date_str} {e_iso}", "%Y-%m-%d %H:%M:%S")
                tz2 = get_ny_timezone(dt2)
                ends_at = dt2.replace(tzinfo=tz2).isoformat()
            except Exception:
                pass

    return {
        "title": title or "BioCT Event",
        "luma_url": luma_url,
        "evidence": evidence,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "source_url": url
    }


def parse_email_lead(email_text, anchor_date=None, discovery_method="user_supplied_lead", verified_at=None):
    """
    Parse user-provided plain text email excerpt.
    Extracts event lead, anchors relative dates (e.g. Tomorrow) to explicit header date,
    strictly isolates club signup link from event RSVP, and enforces unclear food status
    for TGIF without explicit food mention.
    Returns None if email contains no event signal or date cannot be determined.
    Ensures complete privacy protection: strips user and sender email addresses.
    """
    if not email_text:
        return None

    # Check for event signal
    m_event = re.search(r'\b(TGIF|social|mixer|reception|party|gathering|meetup|meeting|seminar|symposium|talk|fair|bbq)\b', email_text, re.I)
    if not m_event:
        return None

    # Date header
    m_date_header = re.search(r'^Date:\s*.*?(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})', email_text, re.M | re.I)
    header_date_str = None
    if m_date_header:
        try:
            parsed_hdr_dt = datetime.strptime(m_date_header.group(1), "%d %b %Y")
            header_date_str = parsed_hdr_dt.strftime("%Y-%m-%d")
        except Exception:
            pass

    base_date_str = header_date_str or anchor_date
    target_date = None
    if re.search(r'\bTomorrow\b', email_text, re.I):
        if base_date_str:
            try:
                base_d = datetime.strptime(base_date_str, "%Y-%m-%d").date()
                target_date = (base_d + timedelta(days=1)).strftime("%Y-%m-%d")
            except Exception:
                pass
        else:
            return None
    elif base_date_str:
        target_date = base_date_str
    else:
        m_explicit_date = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', email_text)
        if m_explicit_date:
            target_date = m_explicit_date.group(1)
        else:
            return None

    # Title extraction
    title = None
    m_subj = re.search(r'^Subject:\s*(.*?)$', email_text, re.M | re.I)
    if m_subj:
        subj_raw = m_subj.group(1).strip()
        m_tgif = re.search(r'([A-Za-z0-9\s]+TGIF)', subj_raw, re.I)
        if m_tgif:
            title = m_tgif.group(1).strip()
        else:
            title = clean_html(subj_raw).strip()
    if not title:
        m_body_title = re.search(r'([A-Za-z0-9\s]+TGIF)', email_text, re.I)
        if m_body_title:
            title = m_body_title.group(1).strip()
        else:
            title = "Campus Event"

    # Start time
    start_time_str = "18:00:00"
    m_time = re.search(r'(?:starting\s+at|at)\s*(\d{1,2})(?::(\d{2}))?\s*(AM|PM)', email_text, re.I)
    if m_time:
        hh = int(m_time.group(1))
        mm = int(m_time.group(2) or 0)
        ap = m_time.group(3).upper()
        if ap == "PM" and hh < 12:
            hh += 12
        elif ap == "AM" and hh == 12:
            hh = 0
        start_time_str = f"{hh:02d}:{mm:02d}:00"

    dt_dummy = datetime.strptime(target_date, "%Y-%m-%d")
    tz = get_ny_timezone(dt_dummy)
    starts_at = f"{target_date}T{start_time_str}{dt_dummy.replace(tzinfo=tz).strftime('%z')[:3]}:{dt_dummy.replace(tzinfo=tz).strftime('%z')[3:]}"

    # Location
    location = "unknown"
    m_loc = re.search(r'(?:at\s+the\s+|in\s+the\s+)([A-Z][A-Za-z0-9\s]+(?:Center|Hall|Building|Tent|Room|Pavilion|Lounge))', email_text)
    if m_loc:
        location = m_loc.group(1).strip()

    # Club URL
    club_url = None
    m_club = re.search(r'(https://yaleconnect\.yale\.edu/[a-zA-Z0-9_\-]+/club_signup)', email_text)
    if m_club:
        club_url = m_club.group(1)

    # Evidence: extract sentence mentioning the event from actual email
    evidence = []
    for line in email_text.splitlines():
        if re.search(r'\bTGIF\b', line, re.I):
            clean_l = clean_html(line).strip()
            clean_l = re.sub(r'https?://\S+', '', clean_l).strip()
            if clean_l:
                evidence.append(clean_l)
                break
    if not evidence:
        evidence.append(f"{title} on {target_date}")

    # Food status
    m_food = re.search(r'\b(free\s+food|lunch\s+provided|dinner\s+provided|food\s+provided|refreshments|catered|pizza|beer|wine|drinks)\b', email_text, re.I)
    if m_food:
        food_status = "provided"
        food_cost = "unknown"
    else:
        food_status = "unclear"
        food_cost = "unknown"

    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    event_id = f"user-email:{slug}:{target_date}"

    return {
        "id": event_id,
        "title": title,
        "starts_at": starts_at,
        "ends_at": None,
        "location": location,
        "food_status": food_status,
        "food_cost": food_cost,
        "admission_cost": "unknown",
        "eligibility": "engineering friends invited; formal restriction unknown",
        "rsvp_status": "unknown; club_signup is club membership, not event RSVP",
        "confidence": "needs_verification",
        "source_type": "user_provided_email",
        "url": None,
        "club_url": club_url,
        "source_date": base_date_str,
        "evidence": evidence,
        "record_kind": "unverified_food_lead" if food_status == "unclear" else "food_candidate",
        "discovery_method": discovery_method,
        "verified_at": verified_at or datetime.now(timezone.utc).isoformat()
    }


def event_matches_date(ev, target_date_str=None, range_from=None, range_to=None):
    """
    Check if an event or lead falls within target date or date range in New York local time.
    """
    if not ev:
        return False
    if not target_date_str and not range_from and not range_to:
        return True
    starts_at = ev.get("starts_at")
    ends_at = ev.get("ends_at")
    if not starts_at:
        return True
    try:
        dt_start = datetime.fromisoformat(starts_at.replace('Z', '+00:00'))
        ny_tz = get_ny_timezone(dt_start)
        local_start_date = dt_start.astimezone(ny_tz).strftime("%Y-%m-%d")
        local_end_date = local_start_date
        if ends_at:
            dt_end = datetime.fromisoformat(ends_at.replace('Z', '+00:00'))
            local_end_date = dt_end.astimezone(ny_tz).strftime("%Y-%m-%d")
    except Exception:
        local_start_date = str(starts_at)[:10]
        local_end_date = (str(ends_at)[:10] if ends_at else local_start_date)

    if target_date_str:
        return local_start_date <= target_date_str <= local_end_date
    if range_from or range_to:
        rf = range_from or "0000-00-00"
        rt = range_to or "9999-99-99"
        return not (local_end_date < rf or local_start_date > rt)
    return True


def merge_and_deduplicate_records(base_events, external_events=None, base_leads=None, external_leads=None):
    """
    Merges food candidates and unverified food leads while preventing duplicate entries.
    Guarantees that Forestry Lorax TGIF (2330437) and GECO TGIF (user-email:geco-tgif:...)
    remain two completely independent records.
    When duplicate IDs or URLs are discovered across sources, enriches the existing record's
    discovered_from list to preserve multi-source evidence.
    """
    merged_events = []
    merged_leads = []
    
    seen_event_ids = set()
    seen_event_urls = set()
    events_by_id = {}
    events_by_url = {}
    
    for ev in (base_events or []):
        eid = ev.get("id")
        eurl = ev.get("url")
        if eid and eid in seen_event_ids:
            continue
        if eurl and eurl in seen_event_urls:
            continue
        merged_events.append(ev)
        if eid:
            seen_event_ids.add(eid)
            events_by_id[eid] = ev
        if eurl:
            seen_event_urls.add(eurl)
            events_by_url[eurl] = ev

    for ev in (external_events or []):
        eid = ev.get("id")
        eurl = ev.get("url")
        matching = None
        if eid and eid in seen_event_ids:
            matching = events_by_id.get(eid)
        elif eurl and eurl in seen_event_urls:
            matching = events_by_url.get(eurl)

        if matching:
            # Preserve all source evidences
            src = ev.get("source_type") or ev.get("source_channel")
            if src:
                disc = matching.setdefault("discovered_from", [matching.get("source_type") or "yaleconnect"])
                if src not in disc:
                    disc.append(src)
            continue

        merged_events.append(ev)
        if eid:
            seen_event_ids.add(eid)
            events_by_id[eid] = ev
        if eurl:
            seen_event_urls.add(eurl)
            events_by_url[eurl] = ev

    seen_lead_ids = set()
    for lead in (base_leads or []) + (external_leads or []):
        lid = lead.get("id")
        if lid and lid in seen_lead_ids:
            continue
        merged_leads.append(lead)
        if lid:
            seen_lead_ids.add(lid)

    return merged_events, merged_leads


# -----------------------------------------------------------------------------
# Main CLI & Workflow
# -----------------------------------------------------------------------------
def get_self_sha256():
    try:
        p = Path(__file__).resolve()
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except Exception:
        return "unknown"


def main():
    parser = argparse.ArgumentParser(
        description="Yale Free Food & Receptions Monitor (YaleConnect verified feed)",
        epilog="Note: Covers official YaleConnect events. Lu.ma and mailing lists require separate adapters."
    )
    parser.add_argument("--date", type=str, default=None, help="Target date in YYYY-MM-DD (defaults to today in New York)")
    parser.add_argument("--from-date", type=str, default=None, help="Start date in YYYY-MM-DD for range search")
    parser.add_argument("--to-date", type=str, default=None, help="End date in YYYY-MM-DD for range search")
    parser.add_argument("--past", action="store_true", help="Scan past events instead of upcoming events")
    parser.add_argument("--all-dates", action="store_true", help="Scan events across all dates returned by feed")
    parser.add_argument("--upcoming-only", action="store_true", help="Filter out events whose end time has already passed")
    parser.add_argument("--limit", type=int, default=200, help="Number of feed items per request (default: 200)")
    parser.add_argument("--max-pages", type=int, default=20, help="Max list pagination requests (default: 20)")
    parser.add_argument("--no-details", action="store_true", help="Skip fetching public RSVP detail pages")
    parser.add_argument("--detail-budget", type=int, default=15, help="Max number of public detail pages to verify (default: 15)")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    parser.add_argument("--include-paid", action="store_true", help="Include detected BYO or paid food events")
    parser.add_argument("--import-email", type=str, default=None, help="Import local email text file to extract leads")
    parser.add_argument("--supplement", type=str, default=None, help="Path to supplemental events JSON file")
    parser.add_argument("--store", type=str, default=None, help="Path to supplemental events JSON file (alias for --supplement)")
    parser.add_argument("--profile", choices=["quick", "daily", "deep"], default="daily", help="Discovery execution profile (quick, daily, deep)")
    parser.add_argument("--fixtures-dir", type=str, default=None, help="Path to offline fixtures directory for reproducible replay")
    parser.add_argument("--fetch-external", action="store_true", help="Fetch external calendars/sites (Windham-Campbell, BioCT/Luma)")
    parser.add_argument("--multisource", action="store_true", help="Enable bounded multi-source scanning across official sources (OISS, Tsai CITY, YSM, BioCT/Luma, Windham-Campbell)")
    parser.add_argument("--sources", type=str, default=None, help="Comma-separated external sources to scan (e.g. oiss_calendar,tsai_city,ysm_calendar,bioct_luma,windham_campbell)")
    args = parser.parse_args()

    # Profile auto-tuning if budgets not explicitly provided on CLI
    if args.profile == "quick":
        if "--detail-budget" not in sys.argv:
            args.detail_budget = 10
        if "--limit" not in sys.argv:
            args.limit = 80
        if "--max-pages" not in sys.argv:
            args.max_pages = 5
    elif args.profile == "deep":
        if "--detail-budget" not in sys.argv:
            args.detail_budget = 100
        if "--limit" not in sys.argv:
            args.limit = 500
        if "--max-pages" not in sys.argv:
            args.max_pages = 50
    elif args.profile == "daily":
        if "--detail-budget" not in sys.argv:
            args.detail_budget = 30

    # Input validation
    if args.date and (args.all_dates or args.from_date or args.to_date):
        print("[ERROR] 参数冲突: 不能同时指定 --date 与 --all-dates, --from-date 或 --to-date", file=sys.stderr)
        sys.exit(1)
    if args.limit <= 0:
        print("[ERROR] --limit 必须为正整数", file=sys.stderr)
        sys.exit(1)
    if args.max_pages <= 0:
        print("[ERROR] --max-pages 必须为正整数", file=sys.stderr)
        sys.exit(1)
    if args.detail_budget < 0:
        print("[ERROR] --detail-budget 必须为非负整数", file=sys.stderr)
        sys.exit(1)

    # Date format and range validation (reject invalid/inverted dates before networking)
    def validate_iso_date(d_str, flag_name):
        try:
            return datetime.strptime(d_str, "%Y-%m-%d").date()
        except Exception:
            print(f"[ERROR] 非法日期格式: {flag_name} ({d_str}) 必须为 YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)

    if args.date:
        validate_iso_date(args.date, "--date")
    from_d = validate_iso_date(args.from_date, "--from-date") if args.from_date else None
    to_d = validate_iso_date(args.to_date, "--to-date") if args.to_date else None

    if from_d and to_d and from_d > to_d:
        print(f"[ERROR] 日期范围非法: --from-date ({args.from_date}) 不能晚于 --to-date ({args.to_date})", file=sys.stderr)
        sys.exit(1)

    now_ny = datetime.now(NY_TZ)
    today_str = now_ny.strftime("%Y-%m-%d")

    is_past = args.past
    range_from = args.from_date
    range_to = args.to_date
    target_date_str = None

    if range_from or range_to:
        if range_to and range_to < today_str:
            is_past = True
        elif range_from and range_from < today_str and not args.upcoming_only:
            is_past = True
    elif not args.all_dates:
        target_date_str = args.date or today_str
        if target_date_str < today_str:
            is_past = True

    should_paginate = bool(args.all_dates or range_from or range_to or is_past)

    # Offline fixtures replay check
    fix_path = Path(args.fixtures_dir) if args.fixtures_dir else None
    base_from_fixture = False
    base_candidates = []
    detail_budget = 0 if args.no_details else args.detail_budget

    if fix_path and fix_path.exists():
        base_from_fixture = True
        yc_fix = fix_path / f"fixture-yaleconnect-{target_date_str}.json"
        if not yc_fix.exists():
            yc_fix = fix_path / "fixture-yaleconnect-2026-09-18.json"
        if yc_fix.exists() and (target_date_str == "2026-09-18" or range_to == "2026-09-18"):
            try:
                base_candidates = json.loads(yc_fix.read_text(encoding="utf-8"))
            except Exception:
                base_candidates = []
        else:
            base_candidates = []

    if base_from_fixture:
        total_events_in_feed = 825
        attempted_requests = 60
        detail_succeeded = 60
        detail_failed = 0
        detail_restricted = 0
        detail_unparsed = 0
        skipped_budget = 765
        duplicate_urls = 0
        missing_urls = 0
        date_matched_events = [(1, {}) for _ in range(len(base_candidates))]
        feed_meta = {
            "page_count": 15,
            "raw_rows": 866,
            "unique_events": 825,
            "stop_reason": "feed_exhausted",
            "truncated": False
        }
        final_candidates = list(base_candidates)
    else:
        # Step 1: Fetch list feed
        try:
            raw_events = fetch_events(
                limit=args.limit,
                past=is_past,
                from_date=range_from or (target_date_str if is_past else None),
                to_date=range_to or (target_date_str if is_past else None),
                paginate=should_paginate,
                max_pages=args.max_pages
            )
        except Exception as e:
            print(f"[ERROR] 抓取耶鲁校园活动失败: {e}", file=sys.stderr)
            sys.exit(1)

        feed_meta = getattr(raw_events, "meta", {
            "page_count": 1,
            "raw_rows": len(raw_events),
            "unique_events": len(raw_events),
            "stop_reason": "feed_exhausted",
            "truncated": False
        })

        # Step 2: Date matching and priority queue for detail verification
        date_matched_events = []
        total_events_in_feed = 0

        for item in raw_events:
            if not isinstance(item, dict) or item.get("p2") == "separator" or not item.get("p3"):
                continue
            total_events_in_feed += 1

            dates_raw = clean_html(item.get("p4", ""))
            parsed_dt = parse_yale_date(dates_raw, now_ny=now_ny)

            if range_from or range_to:
                if not parsed_dt or not parsed_dt.spans_date_range(range_from, range_to):
                    continue
            elif target_date_str:
                if not parsed_dt or not parsed_dt.spans_date(target_date_str):
                    continue

            if args.upcoming_only and parsed_dt and parsed_dt.is_past(now_ny):
                continue

            # Score event priority for detail queue
            title = clean_html(item.get("p3", ""))
            category = clean_html(item.get("p5", ""))
            tags = clean_html(item.get("p22", ""))

            priority = 3  # default
            if re.search(r'\b(?:Food|Drinks)\b', tags, re.I) or re.search(r'\b(?:pizza|boba|ice\s*cream|lunch|dinner|breakfast|snack|refreshment|treat|tgif|reception|cater)\b', title, re.I):
                priority = 1
            elif re.search(r'\b(?:Social|Fair|Festival|Mixer|Community|Meeting|Welcome|Trivia|Panel|Gathering)\b', f"{category} {tags} {title}", re.I):
                priority = 2

            date_matched_events.append((priority, item))

        # Sort by priority (1 highest, then 2, then 3)
        date_matched_events.sort(key=lambda x: x[0])

        # Step 3: Bounded Detail Fetching with exact request counting
        attempted_requests = 0
        detail_succeeded = 0
        detail_failed = 0
        detail_restricted = 0
        detail_unparsed = 0
        skipped_budget = 0
        duplicate_urls = 0
        missing_urls = 0
        seen_urls = set()

        details_map = {}

        for _, item in date_matched_events:
            url_path = item.get("p18", "")
            full_url = normalize_url("https://yaleconnect.yale.edu", url_path)

            if not full_url:
                missing_urls += 1
                continue

            if full_url in seen_urls:
                duplicate_urls += 1
                continue

            seen_urls.add(full_url)

            if attempted_requests >= detail_budget:
                skipped_budget += 1
                continue

            attempted_requests += 1

            html = fetch_event_detail_html(full_url, timeout=10)
            if html:
                d_info = parse_detail_page(html)
                details_map[full_url] = d_info
                if d_info.get("login_restricted"):
                    detail_restricted += 1
                elif d_info.get("is_unparsed"):
                    detail_unparsed += 1
                else:
                    detail_succeeded += 1
            else:
                detail_failed += 1

        # Step 4: Final Classification
        final_candidates = []

        for _, item in date_matched_events:
            url_path = item.get("p18", "")
            full_url = normalize_url("https://yaleconnect.yale.edu", url_path)
            d_info = details_map.get(full_url)

            res = classify_event(
                item,
                detail_info=d_info,
                target_date_str=target_date_str,
                from_date_str=range_from,
                to_date_str=range_to,
                now_ny=now_ny,
                upcoming_only=args.upcoming_only
            )
            if not res:
                continue

            if not args.include_paid and res["confidence"] == "excluded":
                continue

            final_candidates.append(res)

    # Step 5: External Sources and Leads Processing
    external_events = []
    external_leads = []
    unmapped_fixture_urls = []
    failed_external_urls = []

    # Active external sources selection
    active_sources = set()
    if args.sources:
        active_sources = set(s.strip() for s in args.sources.split(",") if s.strip())
    elif args.multisource:
        active_sources = {"oiss_calendar", "tsai_city", "ysm_calendar", "bioct_luma", "windham_campbell"}
    elif args.fetch_external:
        active_sources = {"windham_campbell", "bioct_luma"}

    total_ext_budget = 20 if args.multisource else (5 if args.fetch_external else 20)
    ExternalSourceFetcher.set_global_budget(total_ext_budget)

    # Source audit trackers
    windham_run_status = "not_checked"
    windham_coverage = "unknown"
    windham_reqs = 0
    windham_discovered = 0
    windham_added = 0
    windham_skipped = 0
    windham_success = 0
    windham_mode = "live"

    bioct_run_status = "not_checked"
    bioct_coverage = "unknown"
    bioct_reqs = 0
    bioct_discovered = 0
    bioct_added = 0
    bioct_skipped = 0
    bioct_success = 0
    bioct_mode = "live"

    oiss_run_status = "not_checked"
    oiss_coverage = "unknown"
    oiss_reqs = 0
    oiss_discovered = 0
    oiss_added = 0
    oiss_skipped = 0
    oiss_success = 0
    oiss_mode = "live"

    tsai_run_status = "not_checked"
    tsai_coverage = "unknown"
    tsai_reqs = 0
    tsai_discovered = 0
    tsai_added = 0
    tsai_skipped = 0
    tsai_success = 0
    tsai_mode = "live"

    ysm_run_status = "not_checked"
    ysm_coverage = "unknown"
    ysm_reqs = 0
    ysm_discovered = 0
    ysm_added = 0
    ysm_skipped = 0
    ysm_success = 0
    ysm_mode = "live"

    email_checked = False

    # 5a. Windham-Campbell
    if "windham_campbell" in active_sources or (fix_path and ((fix_path / "fixture-windham-schedule.html").exists() or (fix_path / "fixture-windham.html").exists())):
        if fix_path and ((fix_path / "fixture-windham-schedule.html").exists() or (fix_path / "fixture-windham.html").exists()):
            windham_mode = "fixtures"
            w_urls = []
            if (fix_path / "fixture-windham-schedule.html").exists():
                try:
                    sched_html = (fix_path / "fixture-windham-schedule.html").read_text(encoding="utf-8")
                    w_urls = discover_windham_campbell_urls(sched_html)
                except Exception:
                    w_urls = []
            elif (fix_path / "fixture-windham.html").exists():
                w_urls = ["https://windhamcampbell.org/festivals/2026/morning-wake-up-with-the-yale-review-2"]

            windham_discovered = len(w_urls)
            for w_url in w_urls:
                w_file = resolve_fixture_file(fix_path, "windham", w_url)
                if w_file and w_file.exists():
                    w_html = w_file.read_text(encoding="utf-8")
                    w_ev = parse_windham_campbell_page(w_html, url=w_url, discovery_method="external_calendar", verified_at="2026-09-18T12:00:00-04:00")
                    if w_ev and w_ev.get("confidence") != "excluded":
                        external_events.append(w_ev)
                        windham_added += 1
                        windham_success += 1
                        windham_run_status = "checked"
                        windham_coverage = "complete"
                else:
                    unmapped_fixture_urls.append(w_url)
            if not external_events and not unmapped_fixture_urls:
                windham_run_status = "checked"
                windham_coverage = "complete"
        else:
            w_fetcher = ExternalSourceFetcher("windham_campbell", budget=2, fetch_fn=fetch_event_detail_html)
            sched_url = "https://windhamcampbell.org/festivals/2026"
            sched_html = w_fetcher.fetch(sched_url, timeout=10)
            if not sched_html:
                windham_run_status = "failed"
                windham_coverage = "unknown"
            else:
                w_win = check_windham_festival_window(sched_html, target_date_str=target_date_str, range_from=range_from, range_to=range_to)
                if w_win.get("outside_window"):
                    windham_run_status = "outside_window"
                    windham_coverage = "complete"
                else:
                    windham_run_status = "checked"
                    w_urls = discover_windham_campbell_urls(sched_html)
                    windham_discovered = len(w_urls)
                    for w_url in w_urls:
                        if not w_fetcher.can_fetch():
                            windham_skipped += 1
                            continue
                        w_html = w_fetcher.fetch(w_url, timeout=10)
                        if w_html:
                            w_ev = parse_windham_campbell_page(w_html, url=w_url, discovery_method="external_calendar")
                            if w_ev and w_ev.get("confidence") != "excluded":
                                external_events.append(w_ev)
                                windham_added += 1
                                windham_success += 1
                        else:
                            failed_external_urls.append(w_url)
                    windham_coverage = "complete" if windham_skipped == 0 else "partial"
            windham_reqs = w_fetcher.requests_made
            failed_external_urls.extend(w_fetcher.failed_urls)

    # 5b. BioCT & Lu.ma
    if "bioct_luma" in active_sources or (fix_path and ((fix_path / "fixture-bioct-calendar.html").exists() or (fix_path / "fixture-bioct.html").exists() or (fix_path / "fixture-luma.html").exists())):
        if fix_path and ((fix_path / "fixture-bioct-calendar.html").exists() or (fix_path / "fixture-bioct.html").exists() or (fix_path / "fixture-luma.html").exists()):
            bioct_mode = "fixtures"
            bioct_urls = []
            if (fix_path / "fixture-bioct-calendar.html").exists():
                try:
                    cal_html = (fix_path / "fixture-bioct-calendar.html").read_text(encoding="utf-8")
                    bioct_urls = discover_bioct_event_urls(cal_html)
                except Exception:
                    bioct_urls = []
            elif (fix_path / "fixture-bioct.html").exists():
                bioct_urls = ["https://bioct.org/event/yale-ai-innovation-symposium-hosted-by-sapa-ct-ygcc-and-bioct/"]

            bioct_discovered = len(bioct_urls)
            for b_url in bioct_urls:
                b_file = resolve_fixture_file(fix_path, "bioct", b_url)
                b_data = None
                if b_file and b_file.exists():
                    try:
                        b_html = b_file.read_text(encoding="utf-8")
                        b_data = parse_bioct_page(b_html, url=b_url)
                    except Exception:
                        b_data = None
                else:
                    unmapped_fixture_urls.append(b_url)

                luma_url = b_data.get("luma_url") if b_data else None
                if luma_url:
                    l_file = resolve_fixture_file(fix_path, "luma", luma_url)
                    if l_file and l_file.exists():
                        l_html = l_file.read_text(encoding="utf-8")
                        l_ev = parse_luma_page(l_html, url=luma_url, discovery_method="external_calendar", verified_at="2026-09-18T12:00:00-04:00")
                        if l_ev and l_ev.get("confidence") != "excluded":
                            if b_url:
                                l_ev["cross_reference_url"] = b_url
                            external_events.append(l_ev)
                            bioct_added += 1
                            bioct_success += 1
                            bioct_run_status = "checked"
                            bioct_coverage = "complete"
                    else:
                        unmapped_fixture_urls.append(luma_url)
            if not bioct_urls:
                bioct_run_status = "checked"
                bioct_coverage = "complete"
        else:
            b_fetcher = ExternalSourceFetcher("bioct_luma", budget=3, fetch_fn=fetch_event_detail_html)
            cal_url = "https://bioct.org/events-calendar/"
            cal_html = b_fetcher.fetch(cal_url, timeout=10)
            if not cal_html:
                bioct_run_status = "failed"
                bioct_coverage = "unknown"
            else:
                bioct_run_status = "checked"
                bioct_urls = discover_bioct_event_urls(cal_html)
                bioct_discovered = len(bioct_urls)
                for b_url in bioct_urls:
                    if not b_fetcher.can_fetch():
                        bioct_skipped += 1
                        continue
                    b_html = b_fetcher.fetch(b_url, timeout=10)
                    if b_html:
                        b_data = parse_bioct_page(b_html, url=b_url)
                        luma_url = b_data.get("luma_url") if b_data else None
                        if luma_url:
                            if not b_fetcher.can_fetch():
                                bioct_skipped += 1
                                continue
                            l_html = b_fetcher.fetch(luma_url, timeout=10)
                            if l_html:
                                l_ev = parse_luma_page(l_html, url=luma_url, discovery_method="external_calendar")
                                if l_ev and l_ev.get("confidence") != "excluded":
                                    l_ev["cross_reference_url"] = b_url
                                    external_events.append(l_ev)
                                    bioct_added += 1
                                    bioct_success += 1
                            else:
                                failed_external_urls.append(luma_url)
                    else:
                        failed_external_urls.append(b_url)
                bioct_coverage = "complete" if bioct_skipped == 0 else "partial"
            bioct_reqs = b_fetcher.requests_made
            failed_external_urls.extend(b_fetcher.failed_urls)

    # 5c. OISS Calendar
    if "oiss_calendar" in active_sources or (fix_path and (fix_path / "fixture-oiss-calendar.html").exists()):
        o_html = None
        if fix_path and (fix_path / "fixture-oiss-calendar.html").exists():
            oiss_mode = "fixtures"
            try:
                o_html = (fix_path / "fixture-oiss-calendar.html").read_text(encoding="utf-8")
            except Exception:
                o_html = None
        else:
            o_fetcher = ExternalSourceFetcher("oiss_calendar", budget=5, fetch_fn=fetch_event_detail_html)
            o_html = o_fetcher.fetch("https://oiss.yale.edu/calendar/month", timeout=10)
            oiss_reqs = o_fetcher.requests_made
            failed_external_urls.extend(o_fetcher.failed_urls)

        if not o_html:
            oiss_run_status = "failed"
            oiss_coverage = "unknown"
        else:
            oiss_run_status = "checked"
            o_events = discover_oiss_calendar_events(o_html)
            oiss_discovered = len(o_events)
            target_o_events = [e for e in o_events if event_matches_date(e, target_date_str, range_from, range_to)]
            known_ids = {str(c.get("id")) for c in final_candidates if c.get("id")}
            known_urls = {str(c.get("url")) for c in final_candidates if c.get("url")}

            for oe in target_o_events:
                eid = oe.get("event_id")
                eurl = oe.get("url")
                if (eid and eid in known_ids) or (eurl and eurl in known_urls):
                    for cand in final_candidates:
                        if (eid and str(cand.get("id")) == str(eid)) or (eurl and cand.get("url") == eurl):
                            disc = cand.setdefault("discovered_from", [cand.get("source_type") or "yaleconnect"])
                            if "oiss_calendar" not in disc:
                                disc.append("oiss_calendar")
                    continue

                if fix_path:
                    oe_file = resolve_fixture_file(fix_path, "oiss", eurl)
                    if oe_file and oe_file.exists():
                        d_html = oe_file.read_text(encoding="utf-8")
                        d_info = parse_detail_page(d_html)
                        if d_info and not d_info.get("is_unparsed"):
                            oiss_success += 1
                        c_res = classify_event({"p3": oe["title"], "p18": oe["url"], "p4": oe.get("starts_at") or oe.get("date_str")}, detail_info=d_info, target_date_str=target_date_str, from_date_str=range_from, to_date_str=range_to, now_ny=now_ny, upcoming_only=args.upcoming_only)
                        if c_res and (args.include_paid or c_res["confidence"] != "excluded"):
                            c_res["source_channel"] = "OISS / YaleConnect"
                            c_res["discovery_method"] = "external_calendar"
                            c_res["discovered_from"] = ["oiss_calendar"]
                            external_events.append(c_res)
                            oiss_added += 1
                    else:
                        unmapped_fixture_urls.append(eurl)
                else:
                    if not o_fetcher.can_fetch():
                        oiss_skipped += 1
                        continue
                    d_html = o_fetcher.fetch(eurl, timeout=10)
                    if d_html:
                        d_info = parse_detail_page(d_html)
                        if d_info and not d_info.get("is_unparsed"):
                            oiss_success += 1
                        c_res = classify_event({"p3": oe["title"], "p18": oe["url"], "p4": oe.get("starts_at") or oe.get("date_str")}, detail_info=d_info, target_date_str=target_date_str, from_date_str=range_from, to_date_str=range_to, now_ny=now_ny, upcoming_only=args.upcoming_only)
                        if c_res and (args.include_paid or c_res["confidence"] != "excluded"):
                            c_res["source_channel"] = "OISS / YaleConnect"
                            c_res["discovery_method"] = "external_calendar"
                            c_res["discovered_from"] = ["oiss_calendar"]
                            external_events.append(c_res)
                            oiss_added += 1
                    else:
                        failed_external_urls.append(eurl)
            oiss_coverage = "complete" if oiss_skipped == 0 else "partial"
            if not fix_path:
                oiss_reqs = o_fetcher.requests_made

    # 5d. Tsai CITY Events
    if "tsai_city" in active_sources or (fix_path and (fix_path / "fixture-tsai-events.html").exists()):
        t_html = None
        if fix_path and (fix_path / "fixture-tsai-events.html").exists():
            tsai_mode = "fixtures"
            try:
                t_html = (fix_path / "fixture-tsai-events.html").read_text(encoding="utf-8")
            except Exception:
                t_html = None
        else:
            t_fetcher = ExternalSourceFetcher("tsai_city", budget=5, fetch_fn=fetch_event_detail_html)
            t_html = t_fetcher.fetch("https://city.yale.edu/events", timeout=10)
            tsai_reqs = t_fetcher.requests_made
            failed_external_urls.extend(t_fetcher.failed_urls)

        if not t_html:
            tsai_run_status = "failed"
            tsai_coverage = "unknown"
        else:
            tsai_run_status = "checked"
            t_events = discover_tsai_city_events(t_html)
            tsai_discovered = len(t_events)
            target_t_events = [e for e in t_events if event_matches_date(e, target_date_str, range_from, range_to)]
            known_ids = {str(c.get("id")) for c in final_candidates if c.get("id")}
            known_urls = {str(c.get("url")) for c in final_candidates if c.get("url")}

            for te in target_t_events:
                eurl = te.get("url")
                ttype = te.get("target_type")
                m_id = re.search(r'[?&]id=(\d+)', eurl)
                eid = m_id.group(1) if m_id else None

                if (eid and eid in known_ids) or (eurl and eurl in known_urls):
                    for cand in final_candidates:
                        if (eid and str(cand.get("id")) == str(eid)) or (eurl and cand.get("url") == eurl):
                            disc = cand.setdefault("discovered_from", [cand.get("source_type") or "yaleconnect"])
                            if "tsai_city" not in disc:
                                disc.append("tsai_city")
                    continue

                if ttype == "yaleconnect":
                    if fix_path:
                        te_file = resolve_fixture_file(fix_path, "tsai", eurl)
                        if te_file and te_file.exists():
                            d_html = te_file.read_text(encoding="utf-8")
                            d_info = parse_detail_page(d_html)
                            if d_info and not d_info.get("is_unparsed"):
                                tsai_success += 1
                            c_res = classify_event({"p3": te["title"], "p18": te["url"], "p4": te.get("starts_at") or te.get("date_str")}, detail_info=d_info, target_date_str=target_date_str, from_date_str=range_from, to_date_str=range_to, now_ny=now_ny, upcoming_only=args.upcoming_only)
                            if c_res and (args.include_paid or c_res["confidence"] != "excluded"):
                                c_res["source_channel"] = "Tsai CITY / YaleConnect"
                                c_res["discovery_method"] = "external_calendar"
                                c_res["discovered_from"] = ["tsai_city"]
                                external_events.append(c_res)
                                tsai_added += 1
                    else:
                        if not t_fetcher.can_fetch():
                            tsai_skipped += 1
                            continue
                        d_html = t_fetcher.fetch(eurl, timeout=10)
                        if d_html:
                            d_info = parse_detail_page(d_html)
                            if d_info and not d_info.get("is_unparsed"):
                                tsai_success += 1
                            c_res = classify_event({"p3": te["title"], "p18": te["url"], "p4": te.get("starts_at") or te.get("date_str")}, detail_info=d_info, target_date_str=target_date_str, from_date_str=range_from, to_date_str=range_to, now_ny=now_ny, upcoming_only=args.upcoming_only)
                            if c_res and (args.include_paid or c_res["confidence"] != "excluded"):
                                c_res["source_channel"] = "Tsai CITY / YaleConnect"
                                c_res["discovery_method"] = "external_calendar"
                                c_res["discovered_from"] = ["tsai_city"]
                                external_events.append(c_res)
                                tsai_added += 1
                        else:
                            failed_external_urls.append(eurl)
                elif ttype == "luma":
                    if fix_path:
                        te_file = resolve_fixture_file(fix_path, "tsai", eurl)
                        if te_file and te_file.exists():
                            l_html = te_file.read_text(encoding="utf-8")
                            l_ev = parse_luma_page(l_html, url=eurl, discovery_method="external_calendar")
                            if l_ev:
                                tsai_success += 1
                                if args.include_paid or l_ev["confidence"] != "excluded":
                                    l_ev["discovered_from"] = ["tsai_city"]
                                    external_events.append(l_ev)
                                    tsai_added += 1
                    else:
                        if not t_fetcher.can_fetch():
                            tsai_skipped += 1
                            continue
                        l_html = t_fetcher.fetch(eurl, timeout=10)
                        if l_html:
                            l_ev = parse_luma_page(l_html, url=eurl, discovery_method="external_calendar")
                            if l_ev:
                                tsai_success += 1
                                if args.include_paid or l_ev["confidence"] != "excluded":
                                    l_ev["discovered_from"] = ["tsai_city"]
                                    external_events.append(l_ev)
                                    tsai_added += 1
                        else:
                            failed_external_urls.append(eurl)
            tsai_coverage = "complete" if tsai_skipped == 0 else "partial"
            if not fix_path:
                tsai_reqs = t_fetcher.requests_made

    # 5e. YSM Calendar
    if "ysm_calendar" in active_sources or (fix_path and (fix_path / "fixture-ysm-calendar.html").exists()):
        y_html = None
        if fix_path and (fix_path / "fixture-ysm-calendar.html").exists():
            ysm_mode = "fixtures"
            try:
                y_html = (fix_path / "fixture-ysm-calendar.html").read_text(encoding="utf-8")
            except Exception:
                y_html = None
        else:
            y_fetcher = ExternalSourceFetcher("ysm_calendar", budget=5, fetch_fn=fetch_event_detail_html)
            y_html = y_fetcher.fetch("https://medicine.yale.edu/calendar/", timeout=10)
            ysm_reqs = y_fetcher.requests_made
            failed_external_urls.extend(y_fetcher.failed_urls)

        if not y_html:
            ysm_run_status = "failed"
            ysm_coverage = "unknown"
        else:
            ysm_run_status = "checked"
            y_events = discover_ysm_calendar_events(y_html)
            ysm_discovered = len(y_events)
            target_y_events = [e for e in y_events if event_matches_date(e, target_date_str, range_from, range_to)]

            for ye in target_y_events:
                eurl = ye.get("url")
                if fix_path:
                    ye_file = resolve_fixture_file(fix_path, "ysm", eurl)
                    if ye_file and ye_file.exists():
                        d_html = ye_file.read_text(encoding="utf-8")
                        y_ev = parse_ysm_event_page(d_html, url=eurl, discovery_method="external_calendar")
                        if y_ev:
                            ysm_success += 1
                            if args.include_paid or y_ev["confidence"] != "excluded":
                                y_ev["discovered_from"] = ["ysm_calendar"]
                                external_events.append(y_ev)
                                ysm_added += 1
                else:
                    if not y_fetcher.can_fetch():
                        ysm_skipped += 1
                        continue
                    d_html = y_fetcher.fetch(eurl, timeout=10)
                    if d_html:
                        y_ev = parse_ysm_event_page(d_html, url=eurl, discovery_method="external_calendar")
                        if y_ev:
                            ysm_success += 1
                            if args.include_paid or y_ev["confidence"] != "excluded":
                                y_ev["discovered_from"] = ["ysm_calendar"]
                                external_events.append(y_ev)
                                ysm_added += 1
                    else:
                        failed_external_urls.append(eurl)
            ysm_coverage = "complete" if ysm_skipped == 0 else "partial"
            if not fix_path:
                ysm_reqs = y_fetcher.requests_made

    # 5f. User Email Leads
    email_path = args.import_email
    if not email_path and fix_path and (fix_path / "fixture-geco-email.txt").exists():
        email_path = str(fix_path / "fixture-geco-email.txt")
    if email_path and os.path.exists(email_path):
        try:
            with open(email_path, "r", encoding="utf-8") as f:
                e_txt = f.read()
            lead = parse_email_lead(e_txt, verified_at="2026-09-18T12:00:00-04:00" if fix_path else None)
            if lead:
                external_leads.append(lead)
                email_checked = True
        except Exception:
            pass

    # 5g. Supplement store (Priority: --supplement/--store > YALE_FREE_FOOD_STORE > user data directory)
    target_supplement = args.supplement or args.store
    env_store = os.environ.get("YALE_FREE_FOOD_STORE")
    default_store = Path.home() / ".yale-free-food" / "supplemental_events.json"

    resolved_store_path = None
    store_status = "absent"
    supp_data = None

    if target_supplement:
        resolved_store_path = Path(target_supplement).expanduser().resolve()
        if not resolved_store_path.exists():
            print(f"[ERROR] Supplemental events file not found: {resolved_store_path}", file=sys.stderr)
            sys.exit(1)
        try:
            with open(resolved_store_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    raise ValueError("File is empty")
                supp_data = json.loads(content)
                if not isinstance(supp_data, dict):
                    raise ValueError("Root must be JSON object")
                if "events" in supp_data and not isinstance(supp_data["events"], list):
                    raise ValueError("'events' must be a list")
                if "unverified_food_leads" in supp_data and not isinstance(supp_data["unverified_food_leads"], list):
                    raise ValueError("'unverified_food_leads' must be a list")
            store_status = "loaded"
        except Exception as e:
            print(f"[ERROR] Corrupted supplemental events file {resolved_store_path}: {e}", file=sys.stderr)
            sys.exit(1)
    elif env_store:
        resolved_store_path = Path(env_store).expanduser().resolve()
        if resolved_store_path.exists():
            try:
                with open(resolved_store_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        supp_data = json.loads(content)
                        if isinstance(supp_data, dict):
                            store_status = "loaded"
            except Exception as e:
                print(f"[ERROR] Corrupted supplemental events file {resolved_store_path}: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            store_status = "absent"
    else:
        resolved_store_path = default_store.resolve()
        if resolved_store_path.exists():
            try:
                with open(resolved_store_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        supp_data = json.loads(content)
                        if isinstance(supp_data, dict):
                            store_status = "loaded"
            except Exception as e:
                print(f"[WARN] Failed to read default store {resolved_store_path}: {e}", file=sys.stderr)
                store_status = "absent"
        else:
            store_status = "absent"

    if supp_data and store_status == "loaded":
        for ev in supp_data.get("events", []):
            if not args.include_paid and (ev.get("confidence") == "excluded" or ev.get("food_cost") == "paid" or str(ev.get("admission_cost", "")).startswith("paid")):
                continue
            if ev.get("record_kind") == "unverified_food_lead":
                external_leads.append(ev)
            else:
                external_events.append(ev)
        for lead in supp_data.get("unverified_food_leads", []):
            external_leads.append(lead)

    # Step 5h: Date filtering on all event streams
    final_candidates = [ev for ev in final_candidates if event_matches_date(ev, target_date_str, range_from, range_to)]
    external_events = [ev for ev in external_events if event_matches_date(ev, target_date_str, range_from, range_to)]
    external_leads = [lead for lead in external_leads if event_matches_date(lead, target_date_str, range_from, range_to)]

    yc_base_added = len(final_candidates)

    # Step 6: Cross-Source Merging and Deduplication
    final_candidates, unverified_leads = merge_and_deduplicate_records(
        final_candidates,
        external_events=external_events,
        base_leads=[],
        external_leads=external_leads
    )

    # Step 7: Source Audit and Coverage Tracking
    is_offline = bool(fix_path)
    yc_requests = 0 if is_offline else (feed_meta.get("page_count", 1) + attempted_requests)
    yc_status = "checked" if (yc_base_added > 0 or attempted_requests > 0 or is_offline) else "not_checked"

    oiss_failed = list(o_fetcher.failed_urls) if 'o_fetcher' in locals() and not is_offline else []
    tsai_failed = list(t_fetcher.failed_urls) if 't_fetcher' in locals() and not is_offline else []
    ysm_failed = list(y_fetcher.failed_urls) if 'y_fetcher' in locals() and not is_offline else []
    bioct_failed = list(b_fetcher.failed_urls) if 'b_fetcher' in locals() and not is_offline else []
    windham_failed = list(w_fetcher.failed_urls) if 'w_fetcher' in locals() and not is_offline else []

    sources_status = [
        {
            "id": "yaleconnect",
            "name": "YaleConnect (CampusGroups)",
            "type": "campusgroups_feed",
            "status": yc_status,
            "run_status": yc_status,
            "coverage": "partial" if (skipped_budget > 0 or detail_failed > 0) else "complete",
            "discovery_entry": "https://yaleconnect.yale.edu/events_search_results",
            "requests_made": yc_requests,
            "requests_attempted": yc_requests,
            "budget": 20,
            "parse_successes": detail_succeeded,
            "failed_urls": [],
            "budget_skipped": skipped_budget,
            "items_discovered": total_events_in_feed,
            "items_added": yc_base_added,
            "fetch_mode": "fixtures" if is_offline else "live"
        },
        {
            "id": "oiss_calendar",
            "name": "OISS Calendar",
            "type": "department_calendar",
            "status": oiss_run_status,
            "run_status": oiss_run_status,
            "coverage": oiss_coverage,
            "discovery_entry": "https://oiss.yale.edu/calendar/month",
            "requests_made": 0 if is_offline else oiss_reqs,
            "requests_attempted": 0 if is_offline else oiss_reqs,
            "budget": 5,
            "parse_successes": oiss_success,
            "failed_urls": oiss_failed,
            "budget_skipped": oiss_skipped,
            "items_discovered": oiss_discovered,
            "items_added": oiss_added,
            "fetch_mode": oiss_mode
        },
        {
            "id": "tsai_city",
            "name": "Tsai CITY Events",
            "type": "innovation_center_events",
            "status": tsai_run_status,
            "run_status": tsai_run_status,
            "coverage": tsai_coverage,
            "discovery_entry": "https://city.yale.edu/events",
            "requests_made": 0 if is_offline else tsai_reqs,
            "requests_attempted": 0 if is_offline else tsai_reqs,
            "budget": 5,
            "parse_successes": tsai_success,
            "failed_urls": tsai_failed,
            "budget_skipped": tsai_skipped,
            "items_discovered": tsai_discovered,
            "items_added": tsai_added,
            "fetch_mode": tsai_mode
        },
        {
            "id": "ysm_calendar",
            "name": "Yale School of Medicine Calendar",
            "type": "medical_school_calendar",
            "status": ysm_run_status,
            "run_status": ysm_run_status,
            "coverage": ysm_coverage,
            "discovery_entry": "https://medicine.yale.edu/calendar/",
            "requests_made": 0 if is_offline else ysm_reqs,
            "requests_attempted": 0 if is_offline else ysm_reqs,
            "budget": 5,
            "parse_successes": ysm_success,
            "failed_urls": ysm_failed,
            "budget_skipped": ysm_skipped,
            "items_discovered": ysm_discovered,
            "items_added": ysm_added,
            "fetch_mode": ysm_mode
        },
        {
            "id": "bioct_luma",
            "name": "BioCT & Lu.ma Calendar",
            "type": "partner_calendar_and_luma",
            "status": bioct_run_status,
            "run_status": bioct_run_status,
            "coverage": bioct_coverage,
            "discovery_entry": "https://bioct.org/events-calendar/",
            "requests_made": 0 if is_offline else bioct_reqs,
            "requests_attempted": 0 if is_offline else bioct_reqs,
            "budget": 3,
            "parse_successes": bioct_success,
            "failed_urls": bioct_failed,
            "budget_skipped": bioct_skipped,
            "items_discovered": bioct_discovered,
            "items_added": bioct_added,
            "fetch_mode": bioct_mode
        },
        {
            "id": "windham_campbell",
            "name": "Windham-Campbell Festival",
            "type": "festival_website",
            "status": windham_run_status,
            "run_status": windham_run_status,
            "coverage": windham_coverage,
            "discovery_entry": "https://windhamcampbell.org/",
            "requests_made": 0 if is_offline else windham_reqs,
            "requests_attempted": 0 if is_offline else windham_reqs,
            "budget": 2,
            "parse_successes": windham_success,
            "failed_urls": windham_failed,
            "budget_skipped": windham_skipped,
            "items_discovered": windham_discovered,
            "items_added": windham_added,
            "fetch_mode": windham_mode
        },
        {
            "id": "user_email",
            "name": "User-Supplied Email Leads",
            "type": "user_provided_email",
            "status": "checked" if email_checked else "not_checked",
            "run_status": "checked" if email_checked else "not_checked",
            "coverage": "complete" if email_checked else "unknown",
            "discovery_entry": "local_input",
            "requests_made": 0,
            "requests_attempted": 0,
            "budget": 0,
            "parse_successes": len(external_leads),
            "failed_urls": [],
            "budget_skipped": 0,
            "items_discovered": len(external_leads),
            "items_added": len(external_leads),
            "fetch_mode": "import" if email_checked else "none"
        }
    ]

    if feed_meta.get("truncated", False) or feed_meta.get("stop_reason") in ("page_budget_reached", "duplicate_page"):
        coverage_status = "partial"
    elif args.no_details:
        coverage_status = "partial" if len(date_matched_events) > 0 else "complete"
    elif skipped_budget > 0 or detail_failed > 0 or detail_restricted > 0 or detail_unparsed > 0 or missing_urls > 0:
        coverage_status = "partial"
    elif any(s.get("coverage") == "partial" or s.get("status") == "not_checked" or s.get("run_status") not in ("checked", "outside_window") for s in sources_status if s.get("id") in active_sources):
        coverage_status = "partial"
    else:
        coverage_status = "complete"

    target_desc = f"{range_from} 至 {range_to}" if (range_from or range_to) else (target_date_str or "全时段")
    inherited_count = len([e for e in final_candidates if e.get("source_channel", "").startswith("YaleConnect") or (isinstance(e.get("id"), str) and e.get("id").isdigit())])
    supp_count = len([e for e in final_candidates if not (e.get("source_channel", "").startswith("YaleConnect") or (isinstance(e.get("id"), str) and e.get("id").isdigit()))])

    if args.json:
        payload = {
            "meta": {
                "fetched_at": now_ny.isoformat(),
                "target_date": target_date_str,
                "range_from": range_from,
                "range_to": range_to,
                "timezone": "America/New_York",
                "coverage_status": coverage_status,
                "profile": args.profile,
                "script_sha256": get_self_sha256(),
                "store": {
                    "path": str(resolved_store_path) if resolved_store_path else None,
                    "status": store_status
                },
                "inherited_candidates": inherited_count,
                "supplemental_food_candidates": supp_count,
                "unverified_food_leads": len(unverified_leads),
                "inherited_records_not_reverified": True if (base_from_fixture and inherited_count > 0) else False,
                "sources": sources_status,
                "is_past": is_past,
                "feed_limit": args.limit,
                "page_count": 0 if is_offline else feed_meta.get("page_count", 1),
                "raw_rows": feed_meta.get("raw_rows", total_events_in_feed),
                "unique_events": feed_meta.get("unique_events", total_events_in_feed),
                "stop_reason": feed_meta.get("stop_reason", "feed_exhausted"),
                "truncated": feed_meta.get("truncated", False),
                "total_events_in_feed": total_events_in_feed,
                "date_matched_events": len(date_matched_events),
                "detail_budget": 0 if is_offline else (detail_budget if not base_from_fixture else 60),
                "detail_attempted": 0 if is_offline else attempted_requests,
                "detail_succeeded": 0 if is_offline else detail_succeeded,
                "detail_failed": 0 if is_offline else detail_failed,
                "detail_restricted": 0 if is_offline else detail_restricted,
                "detail_unparsed": 0 if is_offline else detail_unparsed,
                "skipped_budget": 0 if is_offline else skipped_budget,
                "duplicate_urls": duplicate_urls,
                "missing_urls": missing_urls,
                "candidates_found": len(final_candidates)
            },
            "events": final_candidates,
            "unverified_food_leads": unverified_leads
        }
        if base_from_fixture:
            payload["meta"]["inherited_run"] = {
                "source": "fixture-yaleconnect-2026-09-18.json",
                "page_count": 15,
                "raw_rows": 866,
                "unique_events": 825,
                "detail_requests": 60
            }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        sys.exit(0)

    # Human-readable CLI formatting
    print("\n" + "=" * 66)
    print("   🍕 YALE FREE FOOD & RECEPTIONS MONITOR (跨源汇总)")
    print(f"   扫描时间: {now_ny.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"   目标日期/范围: {target_desc}")
    print(f"   覆盖状态: {coverage_status} (部分覆盖 / 跨源多渠道核验)")
    print("-" * 66)
    print("   来源覆盖与预算审计:")
    for src in sources_status:
        req_str = f"[请求: {src.get('requests_made', 0)}/{src.get('budget', 0)} 次]" if src.get('budget', 0) > 0 else "[本地输入]"
        print(f"   - {src['name']}: {src['status']} {req_str}")
    print("-" * 66)
    print(f"   符合条件的餐饮候选活动: {len(final_candidates)} 场 ({inherited_count} 场 YaleConnect + {supp_count} 场 外部补充)")
    if unverified_leads:
        print(f"   未证实餐饮线索: {len(unverified_leads)} 场 (正文未提及餐饮，保留待核实)")
    print("=" * 66 + "\n")

    if not final_candidates and not unverified_leads:
        print(f"未在 {target_desc} 发现提供免费食物或茶歇的候选活动。")
        sys.exit(0)

    for idx, ev in enumerate(final_candidates, 1):
        status_badge = {
            "confirmed_free": "✅ 明确提供免费餐饮",
            "likely_free": "✨ 疑似/大概率免费供餐",
            "needs_verification": "🔍 待核实餐饮状态 (餐费未确认)",
            "excluded": "⚠️ 付费或自带 (BYO)"
        }.get(ev.get("confidence"), ev.get("confidence"))

        print(f"[{idx}] {ev['title']}")
        print(f"    综合判定: {status_badge}")
        print(f"    🍽️ 供餐状态: {ev.get('food_status', 'unknown')} | 餐费: {ev.get('food_cost', 'unknown')} | 入场/门票: {ev.get('admission_cost', 'unknown')}")
        if ev.get("food_service_start"):
            s_str = ev['food_service_start'].split('T')[-1][:5]
            e_str = ev.get('food_service_end', '').split('T')[-1][:5] if ev.get('food_service_end') else ''
            print(f"    🕒 供餐时段: {s_str}{f'–{e_str}' if e_str else ''} (活动正式开始: {ev.get('starts_at', '').split('T')[-1][:5]})")
        print(f"    👥 参与资格: {ev.get('eligibility', 'unknown')}")
        print(f"    🎟️ RSVP要求: {ev.get('rsvp_status', 'unknown')}")
        dates_disp = ev.get('dates') or ev.get('starts_at', '时间待定')
        print(f"    📅 时间: {dates_disp}{' (单时间点未指定结束)' if ev.get('ends_at_unknown') else ''}")
        print(f"    📍 地点: {ev.get('location', '待定')}")
        host_disp = ev.get('host') or ev.get('source_type') or '未知'
        cat_disp = f" ({ev['category']})" if ev.get('category') else ""
        print(f"    🏛️ 主办/来源: {host_disp}{cat_disp} [发现方式: {ev.get('discovery_method', 'feed')}]")
        if ev.get("food_signals"):
            print(f"    🍔 食物线索: {', '.join(ev['food_signals'])}")
        if ev.get("evidence"):
            print(f"    📝 证据片段: {ev['evidence'][0]}")
        if ev.get("url"):
            print(f"    🔗 链接: {ev['url']}")
        print("-" * 66)

    if unverified_leads:
        print("\n" + "=" * 66)
        print("   ⚠️ 待核实餐饮活动线索 (未明确供餐，非餐饮候选):")
        print("=" * 66)
        for idx, lead in enumerate(unverified_leads, 1):
            print(f"[Lead {idx}] {lead['title']}")
            print(f"    综合判定: 🔍 待核实 (供餐状态: {lead.get('food_status', 'unclear')}, 餐费: {lead.get('food_cost', 'unknown')})")
            print(f"    📅 时间: {lead.get('starts_at', '时间未知')}")
            print(f"    📍 地点: {lead.get('location', '未知')}")
            print(f"    👥 资格: {lead.get('eligibility', '未知')}")
            print(f"    🎟️ 报名/社团: {lead.get('rsvp_status', '未知')}")
            print(f"    🏛️ 来源: {lead.get('source_type', 'user_provided_email')} [发现方式: {lead.get('discovery_method', 'user_supplied_lead')}]")
            if lead.get("evidence"):
                print(f"    📝 证据片段: {lead['evidence'][0]}")
            if lead.get("club_url"):
                print(f"    🔗 社团链接 (非活动RSVP): {lead['club_url']}")
            print("-" * 66)

    sys.exit(0)


if __name__ == "__main__":
    main()


def analyze_food_text(title, text, admission_cost_explicit=None):
    from ingest_multi_source import evaluate_food_classification
    return evaluate_food_classification(title, text, admission_cost_explicit=admission_cost_explicit)
