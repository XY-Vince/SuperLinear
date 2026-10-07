#!/usr/bin/env python3
"""Search Reddit for recent fast-food promo code intelligence.

Read-only, budget-capped helper for the fastfood-saver skill. Queries public
Reddit JSON search endpoints for brand subreddits and surfaces recent posts
that look like promo-code discussions. Output is *leads*, never verified codes.

Design constraints (mirror yale-free-food governance rules):
- Hard request budget (default 10); failures count against it.
- Honest reporting: failed sources are reported, never silently invented.
- Stderr for progress, stdout for the payload (JSON or human table).
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BRAND_SUBREDDITS = {
    "subway": "subway",
    "mcdonalds": "McDonalds",
    "tacobell": "tacobell",
    "wendys": "wendys",
    "burgerking": "burgerking",
    "dominos": "Dominos",
    "papajohns": "papajohns",
    "chickfila": "ChickFilA",
    "pandaexpress": "PandaExpress",
    "popeyes": "Popeyes",
}

DEAL_QUERY_TERMS = ["promo code", "coupon code", "discount code"]

CODE_RE = re.compile(r"\b[A-Z][A-Z0-9]{3,11}\b")
DEAL_HINT_RE = re.compile(
    r"\b(code|promo|coupon|deal|discount|free|off|expired|working)\b", re.I
)
TIMEFRAMES = ("day", "week", "month", "year")

USER_AGENT = "fastfood-saver-skill/0.1 (read-only public JSON; contact: local skill)"


def fetch_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_codes(text: str) -> list:
    """Return uppercase tokens from text that look like promo codes.

    Filters common English false positives and pure numbers (post IDs, prices).
    """
    stop = {
        "FREE", "CODE", "PROMO", "COUPON", "DEAL", "OFF", "APP", "EDIT", "TLDR",
        "UPDATE", "PSA", "USA", "USA_ONLY", "UPDATE", "FYI", "IMO", "AND", "THE",
        "NEW", "NOW", "HERE", "LINK", "POST", "SUBWAY", "MCDONALDS", "TACOBELL",
    }
    out = []
    for tok in CODE_RE.findall(text or ""):
        if tok in stop or tok.isdigit():
            continue
        if any(ch.isdigit() for ch in tok) or len(tok) >= 5:
            out.append(tok)
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def search_subreddit(sub: str, timeframe: str, limit: int, timeout: float, budget: dict) -> list:
    posts = []
    for term in DEAL_QUERY_TERMS:
        if budget["made"] >= budget["max"]:
            budget["stop_reason"] = "budget exhausted"
            break
        q = urllib.parse.quote(term)
        url = (
            f"https://www.reddit.com/r/{sub}/search.json"
            f"?q={q}&restrict_sr=1&sort=new&t={timeframe}&limit={limit}"
        )
        budget["made"] += 1
        try:
            data = fetch_json(url, timeout)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            budget["failed_urls"].append({"url": url, "error": str(exc)})
            continue
        for child in data.get("data", {}).get("children", []):
            d = child.get("data", {})
            posts.append(
                {
                    "title": d.get("title", ""),
                    "selftext": (d.get("selftext") or "")[:2000],
                    "created_utc": d.get("created_utc"),
                    "permalink": "https://www.reddit.com" + d.get("permalink", ""),
                    "score": d.get("score", 0),
                    "num_comments": d.get("num_comments", 0),
                }
            )
        time.sleep(1.1)  # be polite to the public endpoint
    # dedupe by permalink, keep newest first
    seen, uniq = set(), []
    for p in sorted(posts, key=lambda x: x.get("created_utc") or 0, reverse=True):
        if p["permalink"] not in seen:
            seen.add(p["permalink"])
            uniq.append(p)
    return uniq


def fetch_comments(permalink: str, timeout: float, budget: dict) -> str:
    """Fetch top-level comment bodies for a post (best-guess code location)."""
    url = permalink.rstrip("/") + ".json?limit=40&depth=1"
    budget["made"] += 1
    try:
        data = fetch_json(url, timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        budget["failed_urls"].append({"url": url, "error": str(exc)})
        return ""
    parts = []
    if len(data) > 1:
        for child in data[1].get("data", {}).get("children", []):
            body = child.get("data", {}).get("body")
            if body:
                parts.append(body)
    return "\n".join(parts)[:8000]


def score_post(p: dict) -> int:
    text = p["title"] + " " + p["selftext"]
    s = 0
    if DEAL_HINT_RE.search(text):
        s += 2
    if extract_codes(text):
        s += 3
    if "expired" in text.lower() or "not working" in text.lower():
        s -= 2
    s += min(p.get("num_comments", 0) // 5, 3)
    return s


def run_brand(brand: str, args, budget: dict) -> dict:
    sub = BRAND_SUBREDDITS[brand]
    posts = search_subreddit(sub, args.timeframe, args.limit, args.timeout, budget)
    for p in posts:
        p["candidate_codes"] = extract_codes(p["title"] + " " + p["selftext"])
        p["relevance"] = score_post(p)
    posts = [p for p in posts if p["relevance"] > 0 or p["candidate_codes"]]

    comment_codes = []
    if args.with_comments:
        for p in sorted(posts, key=lambda x: x["relevance"], reverse=True)[: args.top_n]:
            if budget["made"] >= budget["max"]:
                budget["stop_reason"] = "budget exhausted"
                break
            body = fetch_comments(p["permalink"], args.timeout, budget)
            codes = extract_codes(body)
            if codes:
                comment_codes.append({"post": p["title"], "permalink": p["permalink"], "codes": codes})
            time.sleep(1.1)

    return {
        "brand": brand,
        "subreddit": sub,
        "posts_found": len(posts),
        "posts": [
            {
                "title": p["title"],
                "created_utc": p["created_utc"],
                "permalink": p["permalink"],
                "candidate_codes": p["candidate_codes"],
                "relevance": p["relevance"],
                "num_comments": p["num_comments"],
            }
            for p in sorted(posts, key=lambda x: x["relevance"], reverse=True)[: args.top_n]
        ],
        "codes_from_comments": comment_codes,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--brand", choices=sorted(BRAND_SUBREDDITS), help="single brand to scan")
    g.add_argument("--all", action="store_true", help="scan every known brand")
    ap.add_argument("--timeframe", choices=TIMEFRAMES, default="week")
    ap.add_argument("--limit", type=int, default=15, help="posts per search query")
    ap.add_argument("--top-n", type=int, default=5, help="posts reported per brand")
    ap.add_argument("--with-comments", action="store_true", help="also scan comments of top posts")
    ap.add_argument("--max-requests", type=int, default=10, help="hard request budget")
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    brands = sorted(BRAND_SUBREDDITS) if args.all else [args.brand]
    budget = {"made": 0, "max": args.max_requests, "failed_urls": [], "stop_reason": None}

    results = []
    for b in brands:
        if budget["made"] >= budget["max"]:
            budget["stop_reason"] = "budget exhausted"
            break
        print(f"[scan] r/{BRAND_SUBREDDITS[b]} ...", file=sys.stderr)
        results.append(run_brand(b, args, budget))

    payload = {
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "brands_scanned": [r["brand"] for r in results],
        "source_audit": {
            "requests_made": budget["made"],
            "budget_max": budget["max"],
            "stop_reason": budget["stop_reason"],
            "failed_urls": budget["failed_urls"],
        },
        "disclaimer": "Leads only: codes surfaced from Reddit are unverified and may be "
        "expired or region-locked. Verify at checkout before relying on them.",
        "results": results,
    }

    if args.json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        for r in results:
            print(f"\n== {r['brand']} (r/{r['subreddit']}) — {r['posts_found']} relevant posts ==")
            for p in r["posts"]:
                when = time.strftime("%Y-%m-%d", time.gmtime(p["created_utc"] or 0))
                codes = ",".join(p["candidate_codes"]) or "-"
                print(f"  [{when}] {p['title']}")
                print(f"      codes: {codes}  relevance: {p['relevance']}")
                print(f"      {p['permalink']}")
            for c in r["codes_from_comments"]:
                print(f"  comment codes for '{c['post']}': {','.join(c['codes'])}")
        audit = payload["source_audit"]
        print(
            f"\n[audit] requests {audit['requests_made']}/{audit['budget_max']}"
            f"  failures: {len(audit['failed_urls'])}"
            f"  stop: {audit['stop_reason'] or 'completed'}",
            file=sys.stderr,
        )
        if audit["failed_urls"]:
            print("[audit] failed urls recorded in --json output", file=sys.stderr)

    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
