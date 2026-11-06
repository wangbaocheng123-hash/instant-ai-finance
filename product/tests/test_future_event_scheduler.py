"""Prove calendar delivery continues while the pre-existing news loop is blocked."""
import threading
import unittest
from unittest.mock import patch

from instant_ai import server


class FutureEventSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.state = dict(server.COLLECTION_STATE)

    def tearDown(self):
        server.COLLECTION_STATE.clear()
        server.COLLECTION_STATE.update(self.state)

    def test_calendar_does_not_wait_for_slow_news_and_registers_received_nodes(self):
        news_entered=threading.Event();release_news=threading.Event();delivered=threading.Event();stop=threading.Event()
        def slow_news():
            news_entered.set();release_news.wait(5);return {}
        def supply():
            delivered.set();return {'state':'connected','delivered':2}
        with patch.object(server,'run_collection',side_effect=slow_news),patch.object(server,'refresh_watch_events',return_value={}),patch.object(server,'dispatch_web_push_notifications',return_value={}),patch.object(server,'supply_future_events',side_effect=supply),patch.object(server,'sync_watch_events') as sync:
            news=threading.Thread(target=server._collect_in_background)
            calendar=threading.Thread(target=server._calendar_scheduler_loop,kwargs={'stop_event':stop,'interval_seconds':300})
            try:
                news.start();self.assertTrue(news_entered.wait(2))
                calendar.start();self.assertTrue(delivered.wait(2),'Calendar is independent of a blocked news collector')
                self.assertFalse(release_news.is_set())
            finally:
                stop.set();release_news.set();news.join(3);calendar.join(3)
            self.assertFalse(calendar.is_alive());self.assertFalse(news.is_alive())
            sync.assert_called_once()

    def test_temporary_failure_cannot_kill_the_next_calendar_tick(self):
        stop=threading.Event()
        count=0
        def tick():
            nonlocal count
            count+=1
            if count==1:raise OSError('synthetic outage')
            stop.set();return {'state':'connected','delivered':0}
        with patch.object(server,'supply_future_events',side_effect=tick) as supply,patch.object(server,'sync_watch_events') as sync:
            server._calendar_scheduler_loop(stop_event=stop,interval_seconds=.001)
            self.assertEqual(supply.call_count,2)
            self.assertEqual(server.COLLECTION_STATE['future_calendar']['state'],'connected')
            sync.assert_not_called()
