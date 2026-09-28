"""Offline probes of generalization, not fixed known-event outputs."""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'scripts'))
import ingest_multi_source as ingest
import discovery

def page(name,description,offers=None,location=None):
    e={'@type':'Event','name':name,'description':description,'startDate':'2030-05-12T14:00:00-04:00','endDate':'2030-05-12T16:00:00-04:00'}
    if offers is not None:e['offers']=offers
    if location is not None:e['location']=location
    return '<script type="application/ld+json">'+json.dumps(e)+'</script>'

records=[]
def record(name,actual,expected,passed):
    records.append(dict(name=name,expected=expected,actual=actual,passed=passed))
r=ingest.parse_eventbrite_html(page('English Institute Future Seminar','No food is provided.',{'price':'80'}),url='https://www.eventbrite.com/e/unseen-2030-123',allow_non_food=True)
record('english_new_event_without_meals',r,'No inherited Reversal, 2026 meal schedule or $0/$20/$50; keep explicit $80.', 'Reversal' not in json.dumps(r) and '$50' not in json.dumps(r))
r=ingest.parse_eventbrite_html(page('Unrelated Workshop','Meet the speakers.',{'lowPrice':'0','highPrice':'80'}),url='https://www.eventbrite.com/e/unrelated-456',allow_non_food=True)
record('generic_price_range',r['admission_cost'],'Preserve $0-$80, no invented $20 or $50', '$50' not in str(r['admission_cost']) and '80' in str(r['admission_cost']))
r=ingest.parse_luma_html(page('Web Privacy Workshop','Learn how browser cookies work. No food is provided.'),url='https://luma.com/unseen-cookies',allow_non_food=True)
record('browser_cookies_not_food',r,'Do not invent Cookies will be provided', r['food_status']!='provided')
r=ingest.parse_luma_html(page('Tsai CITY Offsite','Meet at our offsite.',location={'name':'Tsai CITY offsite: 100 Test Street'}),url='https://luma.com/unseen-offsite',allow_non_food=True)
record('venue_preserves_page',r['location'],'100 Test Street remains', '100 Test Street' in r['location'])
cards=discovery.extract_html_event_cards('<h3><a href="/events/unseen">Ordinary Lecture</a></h3><time datetime="2030-05-12T14:00:00-04:00">May 12, 2030 2:00 pm</time>',base_url='https://dept.yale.edu/events')
record('card_does_not_verify_details_or_invent_cost',cards[0],'details_verified false; admission unknown; no assumed Poorvu host',not cards[0]['details_verified'] and cards[0]['admission_cost']=='unknown')
for r in records: print(r['name'], 'PASS' if r['passed'] else 'FAIL', json.dumps(r['actual'],ensure_ascii=False))
