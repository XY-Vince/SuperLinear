import contextlib
import io
import json
import os
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import build_verified_runtime as pipeline
import fetch_free_food as feed
import discovery
from event_store import EventStore


class TestCloseoutDetails(unittest.TestCase):
    def test_live_executes_detail_queue_and_updates_food_status(self):
        raw = [{
            'p0': 'false',
            'p1': '99001',
            'p2': 'abc1',
            'p3': 'In-Window Innovation Mixer',
            'p4': 'Mon, Sep 28, 2026 5:00 PM – 7:00 PM',
            'p5': 'Campus/Social',
            'p6': '17 Prospect St',
            'p9': 'Innovation Club',
            'p12': '',
            'p18': '/rsvp_boot?id=99001',
            'p22': 'Social, Networking'
        }]

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(feed, 'fetch_events', return_value=raw) as fetch, \
                 patch.object(feed, 'fetch_event_detail_html', return_value='<html>Complimentary pizza will be served.</html>') as detail, \
                 patch.object(discovery, 'execute_run_command'), \
                 contextlib.redirect_stdout(io.StringIO()):
                report = pipeline.run_pipeline(str(Path(tmp) / 'live'), mode='live', from_date='2026-09-27')

            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(detail.call_count, 1)
            agenda = report['weekly']['agenda']
            ev = next((e for e in agenda if e['id'] == '99001'), None)
            self.assertIsNotNone(ev)
            self.assertEqual(ev['food_status'], 'provided')
            self.assertEqual(ev['food_cost'], 'free')
            self.assertTrue(ev['details_verified'])
            self.assertEqual(ev['confidence'], 'confirmed_free')

    def test_replay_mode_does_not_execute_detail_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(feed, 'fetch_event_detail_html', side_effect=RuntimeError('network forbidden in replay')) as detail, \
                 patch.dict(os.environ, {'YFF_CACHE_DIR': str(Path(tmp) / 'cache')}), \
                 patch.object(discovery, 'load_seeds', return_value=[]), \
                 patch('urllib.request.urlopen', side_effect=RuntimeError('network forbidden')), \
                 contextlib.redirect_stdout(io.StringIO()):
                report = pipeline.run_pipeline(str(Path(tmp) / 'replay'), mode='replay', from_date='2026-10-04')

            self.assertEqual(detail.call_count, 0)
            self.assertEqual(report['weekly']['meta']['agenda_count'], 0)


if __name__ == '__main__':
    unittest.main()
