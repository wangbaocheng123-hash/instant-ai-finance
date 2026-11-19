"""Actual Python -> isolated Compass HTTP intake. Synthetic records only."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from instant_ai.monetary_policy import dispatch_policy_signals, policy_snapshot
from tests import test_monetary_policy as policy_fixtures

NOW = policy_fixtures.NOW


@unittest.skipUnless(os.environ.get('COMPASS_SOURCE_ROOT'), 'Set COMPASS_SOURCE_ROOT for the isolated two-system policy intake')
class MonetaryPolicyHTTPTests(unittest.TestCase):
    def test_real_receipt_duplicate_proxy_guard_and_unchanged_financial_state(self):
        root = Path(os.environ['COMPASS_SOURCE_ROOT']).resolve() / 'mcp'
        node = shutil.which('node')
        data = Path(subprocess.check_output([node, '--input-type=module', '-e',
            "import {createSyntheticCompassDataDir} from './test-compass-fixture.mjs';process.stdout.write(createSyntheticCompassDataDir('policy-python-http-'));"], cwd=root, text=True))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        env = {**os.environ, 'NODE_ENV': 'test', 'TIME_COMPASS_TEST_NOW': NOW.isoformat(),
            'TIME_COMPASS_DATA_DIR': str(data), 'TIME_COMPASS_BRIDGE_PORT': str(port),
            'TIME_COMPASS_SKIP_DAILY_SNAPSHOT': '1', 'TIME_COMPASS_SKIP_FUND_ACCRUAL': '1',
            'TIME_COMPASS_SKIP_FUND_CALENDAR': '1', 'TIME_COMPASS_SKIP_NEXT_SESSION': '1',
            'TIME_COMPASS_SKIP_POLICY_PROBABILITIES': '1', 'TIME_COMPASS_SKIP_DECEMBER_HIKE_OBSERVATION': '1'}
        process = subprocess.Popen([node, 'local-bridge.mjs'], cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        fixture = policy_fixtures.MonetaryPolicyTests()
        fixture.setUp()
        def get(route, headers=None):
            with urlopen(Request(base + route, headers=headers or {}), timeout=6) as response:
                return json.load(response)
        try:
            for _ in range(80):
                try:
                    get('/api/health')
                    break
                except OSError:
                    time.sleep(.1)
            else:
                self.fail('Isolated Compass bridge did not start')
            before = get('/api/v1/state')['state']
            assignment = get('/api/v1/watch-events')
            self.assertIn('monetaryPolicyIngestion', assignment)
            proxied = get('/api/v1/watch-events', {'X-Forwarded-For': '203.0.113.7', 'X-Forwarded-Proto': 'https'})
            self.assertNotIn('monetaryPolicyIngestion', proxied)
            fixture.add()
            kwargs = dict(path=fixture.path, now=NOW,
                assignment_fetcher=lambda _: get('/api/v1/watch-events'),
                target_url=base + '/api/v1/integrations/instant-ai/event-signals')
            self.assertEqual(dispatch_policy_signals(**kwargs)['delivered'], 1)
            self.assertEqual(dispatch_policy_signals(**kwargs)['delivered'], 0)
            signals = get('/api/v1/integrations/instant-ai/event-signals', {'Origin': base})['signals']
            self.assertEqual(len(signals), 1)
            signal = signals[0]
            self.assertEqual(signal['signalKind'], 'monetary-policy-candidate')
            self.assertEqual(signal['policyEvidence']['speaker'], 'Christopher Waller')
            self.assertEqual(signal['policyEvidence']['sources'][0]['excerpt'], '')
            self.assertEqual(signal['status'], 'received')
            local = policy_snapshot(path=fixture.path, now=NOW)['signals'][0]
            self.assertEqual(local['status'], 'delivered')
            self.assertIn('等待既有GPT', local['pipelineStatus'])
            body = {'contractVersion': 1, 'sourceSystem': 'instant-ai', 'signalKind': 'monetary-policy-candidate',
                    'deliveryToken': assignment['monetaryPolicyIngestion']['deliveryToken'],
                    'candidate': {key: value for key, value in local.items() if key not in {'itemId', 'status', 'pipelineStatus'}}}
            request = Request(kwargs['target_url'], data=json.dumps(body).encode(), method='POST',
                              headers={'Content-Type': 'application/json', 'X-Forwarded-For': '203.0.113.7'})
            with self.assertRaises(HTTPError) as error:
                urlopen(request, timeout=5)
            self.assertEqual(error.exception.code, 403)
            self.assertEqual(get('/api/v1/state')['state'], before)
            self.assertEqual(get('/api/v1/ai-guidance')['guidance'], [])
        finally:
            process.terminate()
            process.wait(timeout=8)
            fixture.tearDown()
            shutil.rmtree(data)


if __name__ == '__main__':
    unittest.main()
