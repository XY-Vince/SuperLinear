"""Independent offline behavioral probes runner copy for local testing inside sandbox."""
import argparse
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import discovery
from event_store import EventStore
from briefing import generate_briefing, render_briefing_markdown

results = []

def record(name, expected, actual):
    results.append(dict(name=name, expected=expected, actual=actual, meets_requirement=expected == actual))

def run(state, html, parsed=None):
    args = argparse.Namespace(state_dir=str(state), profile='daily', resume=True,
                              from_date='2026-09-27', days=7)
    with patch.object(discovery, 'fetch_url_html', return_value=html) as fetch, \
         patch.object(discovery, 'load_seeds', return_value=[]), \
         patch.object(discovery, 'parse_luma_html', return_value=parsed), \
         contextlib.redirect_stdout(io.StringIO()):
        discovery.execute_run_command(args)
    return fetch.call_count

with tempfile.TemporaryDirectory(prefix='yff-functional-review-') as tmp:
    base = Path(tmp)
    # Raw import claims must not self-certify as verified free food or independent discovery.
    state = base / 'import'
    input_path = base / 'unverified.jsonl'
    input_path.write_text(json.dumps({
        'title': 'Probe Reception', 'result_url': 'https://example.org/event',
        'starts_at': '2026-09-27T17:00:00-04:00', 'food_status': 'provided',
        'food_cost': 'free', 'confidence': 'confirmed_free',
        'evidence_text': 'Food and refreshments served.'}) + '\n')
    args = argparse.Namespace(input=str(input_path), state_dir=str(state))
    with contextlib.redirect_stdout(io.StringIO()):
        discovery.execute_ingest_research_command(args)
        discovery.execute_ingest_research_command(args)
    store = EventStore(state_dir=state)
    ev = store.get_events_for_date('2026-09-27')[0][0]
    record('unverified_import_not_verified', False, bool(ev['details_verified']))
    record('unsupported_free_claim_not_confirmed', False, ev['confidence'] == 'confirmed_free')
    raw = json.loads(ev['raw_json'])
    record('retain_imported_evidence', True, 'evidence_text' in raw or 'evidence' in raw)
    with store._get_conn() as conn:
        count = conn.execute('select count(*) from discovery_ledger where is_independent_new=1').fetchone()[0]
    record('unverified_repeat_not_independent_new', 0, count)

    # A real-shaped generic department page should be extracted, not silently marked done.
    state = base / 'generic'
    store = EventStore(state_dir=state)
    url = 'https://dept.yale.edu/events/probe'
    store.enqueue_url(url)
    html = '<script type="application/ld+json">' + json.dumps({
        '@context': 'https://schema.org', '@type': 'Event', 'name': 'Probe Lunch',
        'startDate': '2026-09-28T12:00:00-04:00', 'endDate': '2026-09-28T13:00:00-04:00',
        'description': 'Complimentary lunch is provided.'}) + '</script>'
    run(state, html)
    record('generic_official_event_extracted', 1, len(store.get_events_for_date('2026-09-28')[0]))

    # Successful event parsing should still expand its organizer links.
    state = base / 'host'
    store = EventStore(state_dir=state)
    url = 'https://luma.com/probe-event'
    host = 'https://luma.com/probe-host'
    store.enqueue_url(url)
    run(state, '<a href="%s">Organizer calendar</a>' % host,
        {'id': 'event', 'title': 'Probe', 'url': url, 'starts_at': '2026-09-28T12:00:00-04:00', 'host_url': host})
    record('parsed_event_expands_host', True, store.get_frontier_item(host) is not None)

    # Discovery outside requested week is not an in-window independent discovery.
    state = base / 'window'
    store = EventStore(state_dir=state)
    store.enqueue_url(url)
    run(state, '', {'id': 'future', 'url': url, 'title': 'Future', 'starts_at': '2026-12-01T12:00:00-05:00'})
    with store._get_conn() as conn:
        count = conn.execute('select count(*) from discovery_ledger where is_independent_new=1').fetchone()[0]
    record('outside_window_not_counted_as_run_gain', 0, count)

    # Existing supplement must survive first SQLite write.
    state = base / 'legacy'
    state.mkdir()
    path = state / 'supplemental_events.json'
    path.write_text(json.dumps({'events': [{'id': 'old', 'title': 'Old lead'}]}))
    store = EventStore(state_dir=state)
    store.upsert_event({'id': 'new', 'title': 'New lead'})
    data = json.loads(path.read_text())
    ids = [e['id'] for e in data['events'] + data['unverified_food_leads']]
    record('legacy_json_survives_first_write', True, 'old' in ids)

    # Multi-day event should remain visible on its second day.
    state = base / 'multiday'
    store = EventStore(state_dir=state)
    store.upsert_event({'id': 'multiday', 'title': 'Two day meeting',
                        'starts_at': '2026-09-27T17:00:00-04:00', 'ends_at': '2026-09-28T18:00:00-04:00'})
    record('multiday_second_day_visible', 1, len(store.get_events_for_date('2026-09-28')[0]))

    # Unknown non-food discovery is kept in JSON but should not disappear from Markdown.
    state = base / 'render'
    store = EventStore(state_dir=state)
    store.upsert_event({'id': 'unclear', 'title': 'Probe hidden agenda row',
                        'starts_at': '2026-09-27T17:00:00-04:00', 'food_status': 'unclear'})
    report = generate_briefing(str(state), '2026-09-27')
    record('all_agenda_records_visible_in_markdown', True, 'Probe hidden agenda row' in render_briefing_markdown(report))

for result in results:
    print(json.dumps(result))
print('Requirements met:', sum(r['meets_requirement'] for r in results), '/', len(results))
