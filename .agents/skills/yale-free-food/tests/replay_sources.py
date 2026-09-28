"""Read-only candidate review: actual saved HTML -> actual runner, isolated temp DBs.
Only fetch and seed loading are substituted. No event-parser mocks or event inserts.
Unarchived URLs fail explicitly; network is disabled process-wide.
"""
import argparse
import contextlib
import hashlib
import io
import json
import re
import socket
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'scripts'))
import discovery
from event_store import EventStore

FILES = {
    'https://city.yale.edu/events': 'city_yale_edu_events.html',
    'https://luma.com/lne1y89w': 'luma_lne1y89w.html',
    'https://luma.com/o8a6285z': 'luma_o8a6285z.html',
    'https://poorvucenter.yale.edu/events': 'poorvucenter_events.html',
    'https://englishinstitute.yale.edu/2026-conference': 'englishinstitute_2026_conference.html',
    'https://www.eventbrite.com/e/the-english-institute-83rd-annual-meeting-tickets-1987005227123': 'eventbrite_english_institute.html',
}
pages = {url: (ROOT / 'data/observations/pages' / name).read_text() for url, name in FILES.items()}

def offline(*a, **kw):
    raise RuntimeError('Network disabled for independent replay')

results = {'scope': 'Frozen HTML replay; missing snapshots are not live HTTP failures',
           'candidate_hashes': {}, 'direct_parsing': [], 'chains': []}
for name in ('discovery.py', 'event_store.py', 'ingest_multi_source.py', 'build_verified_runtime.py'):
    results['candidate_hashes'][name] = hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()

with patch.object(socket.socket, 'connect', side_effect=offline), patch.object(socket, 'create_connection', side_effect=offline):
    for url, html in pages.items():
        parser = discovery.parse_luma_html if 'luma.com' in url else discovery.parse_eventbrite_html if 'eventbrite.com' in url else discovery.parse_jsonld_event
        try:
            rec = parser(html, url=url, allow_non_food=True) if parser != discovery.parse_jsonld_event else parser(html, url=url)
            result = {k: rec.get(k) for k in ('id', 'title', 'starts_at', 'ends_at', 'host_url', 'location', 'food_status', 'food_cost', 'admission_cost')} if rec else None
        except Exception as exc:
            result = {'error': str(exc)}
        jsonlds = re.findall(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>', html, re.S)
        results['direct_parsing'].append({'url': url, 'file': FILES[url], 'sha256': hashlib.sha256(html.encode()).hexdigest(), 'jsonld_script_count': len(jsonlds), 'parsed': result, 'discovered_links': discovery.crawl_page_for_links(html, url)})

    for seed in ('https://city.yale.edu/events', 'https://poorvucenter.yale.edu/events', 'https://englishinstitute.yale.edu/2026-conference'):
        calls = []
        def fetch(url, **kw):
            key = url.rstrip('/')
            calls.append({'url': url, 'snapshot_available': key in pages})
            if key not in pages:
                raise FileNotFoundError('No archived snapshot: ' + url)
            return pages[key]
        with tempfile.TemporaryDirectory(prefix='yff-source-replay-') as tmp:
            args = argparse.Namespace(state_dir=tmp, profile='daily', resume=False, from_date='2026-09-27', days=7)
            stdout = io.StringIO()
            with patch.object(discovery, 'fetch_url_html', side_effect=fetch), patch.object(discovery, 'load_seeds', return_value=[{'entry_url': seed}]), contextlib.redirect_stdout(stdout):
                discovery.execute_run_command(args)
            store = EventStore(state_dir=tmp)
            with store._get_conn() as conn:
                events = [dict(row) for row in conn.execute('SELECT * FROM events')]
                frontier = [dict(row) for row in conn.execute('SELECT * FROM frontier')]
            results['chains'].append({'seed': seed, 'requests': calls, 'events': events, 'frontier': frontier, 'stdout': stdout.getvalue()})

(OUT / 'source-replay.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
for item in results['direct_parsing']:
    print('PARSE', item['file'], json.dumps(item['parsed'], ensure_ascii=False), 'JSONLD', item['jsonld_script_count'])
for chain in results['chains']:
    print('CHAIN', chain['seed'], 'events', len(chain['events']), 'archived_reads', sum(c['snapshot_available'] for c in chain['requests']), 'missing_snapshots', sum(not c['snapshot_available'] for c in chain['requests']))
    print('EVENTS', [(e.get('title'), e.get('starts_at')) for e in chain['events']])
