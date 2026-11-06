"""Opt-in real Python supplier -> Node Compass bridge, with synthetic data only."""
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
from unittest.mock import patch
from urllib.request import urlopen

from instant_ai.database import initialize
from instant_ai.future_events import supply_future_events
from tests.test_future_events import NOW, TSMC, RSS, BLS, SOURCES


@unittest.skipUnless(os.environ.get('COMPASS_SOURCE_ROOT'), 'Set COMPASS_SOURCE_ROOT for the two-service integration acceptance')
class CompassCalendarIntegrationTests(unittest.TestCase):
    def test_real_http_receipts_restart_and_outbox_repair(self):
        root=Path(os.environ['COMPASS_SOURCE_ROOT']).resolve()
        node=shutil.which('node')
        data=Path(subprocess.check_output([node,'--input-type=module','-e',"import {createSyntheticCompassDataDir} from './test-compass-fixture.mjs';process.stdout.write(createSyntheticCompassDataDir('compass-python-e2e-'));"],cwd=root/'mcp',text=True))
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        env={**os.environ,'NODE_ENV':'test','TIME_COMPASS_TEST_NOW':NOW.isoformat(),'TIME_COMPASS_DATA_DIR':str(data),'TIME_COMPASS_BRIDGE_PORT':str(port),
             'TIME_COMPASS_SKIP_DAILY_SNAPSHOT':'1','TIME_COMPASS_SKIP_FUND_ACCRUAL':'1','TIME_COMPASS_SKIP_FUND_CALENDAR':'1','TIME_COMPASS_SKIP_NEXT_SESSION':'1','TIME_COMPASS_SKIP_POLICY_PROBABILITIES':'1','TIME_COMPASS_SKIP_DECEMBER_HIKE_OBSERVATION':'1'}
        process=None
        def get(route):
            with urlopen(base+route,timeout=3) as response:return json.load(response)
        def start():
            nonlocal process
            process=subprocess.Popen([node,'local-bridge.mjs'],cwd=root/'mcp',env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            for _ in range(100):
                try:
                    if get('/api/health'):return
                except OSError:pass
                time.sleep(.1)
            self.fail('Isolated Compass bridge did not start')
        def stop():
            if process and process.poll() is None:process.terminate();process.wait(timeout=8)
        try:
            start()
            before=get('/api/v1/state')['state']
            bodies={s['url']:b for s,b in zip(SOURCES,[TSMC,RSS,BLS])}
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'instant-ai.db';initialize(path)
                def run():
                    with patch('instant_ai.future_events._sources',return_value=SOURCES),patch.dict(os.environ,{'INSTANT_AI_COMPASS_EVENTS_URL':base+'/api/v1/watch-events'}):
                        return supply_future_events(path=path,now=NOW,fetcher=lambda u:{'status':200,'body':bodies[u]},target_url=base+'/api/v1/integrations/instant-ai/calendar')
                self.assertEqual(run()['delivered'],3)
                page=get('/api/v1/ai-guidance')
                self.assertEqual(len(page['homeAiTimeline']),2)
                self.assertEqual(len(page['zijinIncomingTimeline']),1)
                self.assertEqual(page['instantAiSupply']['state'],'connected')
                self.assertEqual(run()['delivered'],0)
                stop();start()
                self.assertEqual(len(get('/api/v1/ai-guidance')['homeAiTimeline']),2)
                self.assertEqual(run()['delivered'],0,'New token after restart must preserve receipts')
                stop()
                # Simulate a receiver restore missing its calendar; financial state stays intact.
                for suffix in ['', '-wal', '-shm']:(data/('instant-ai-calendar.db'+suffix)).unlink(missing_ok=True)
                start()
                self.assertEqual(run()['delivered'],3,'Receiver inventory repairs an acknowledged outbox')
                self.assertEqual(get('/api/v1/state')['state'],before)
        finally:
            stop();shutil.rmtree(data)


if __name__=='__main__':unittest.main()
