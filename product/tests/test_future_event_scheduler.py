"""Prove calendar delivery continues while the pre-existing news loop is blocked."""
import threading
import unittest
from unittest.mock import patch

from instant_ai import server


class FutureEventSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.state = dict(server.COLLECTION_STATE)
        self.policy_patch = patch.object(server, 'dispatch_policy_signals', return_value={'state': 'idle', 'delivered': 0})
        self.policy_patch.start()

    def tearDown(self):
        self.policy_patch.stop()
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
        with patch.object(server,'supply_future_events',side_effect=tick) as supply,patch.object(server,'sync_watch_events') as sync,patch.object(server,'dispatch_web_push_notifications',return_value={}):
            server._calendar_scheduler_loop(stop_event=stop,interval_seconds=.001)
            self.assertEqual(supply.call_count,2)
            self.assertEqual(server.COLLECTION_STATE['future_calendar']['state'],'connected')
            sync.assert_not_called()

    def test_push_failure_preserves_discovery_state_and_is_reported_separately(self):
        stop=threading.Event()
        def tick():
            stop.set()
            return {'state':'connected','delivered':0}
        with patch.object(server,'supply_future_events',side_effect=tick),patch.object(server,'dispatch_web_push_notifications',side_effect=OSError('synthetic push outage')):
            server._calendar_scheduler_loop(stop_event=stop)
        self.assertEqual(server.COLLECTION_STATE['future_calendar']['state'],'connected')
        self.assertEqual(server.COLLECTION_STATE['future_calendar']['notification_error'],'OSError')

    def test_policy_delivery_failure_does_not_disrupt_calendar_sync_or_notifications(self):
        stop=threading.Event()
        def tick():
            stop.set()
            return {'state':'connected','delivered':1}
        with patch.object(server,'supply_future_events',side_effect=tick),patch.object(server,'dispatch_policy_signals',side_effect=OSError('synthetic policy receiver outage')),patch.object(server,'sync_watch_events') as sync,patch.object(server,'dispatch_web_push_notifications',return_value={}) as push:
            server._calendar_scheduler_loop(stop_event=stop)
        self.assertEqual(server.COLLECTION_STATE['future_calendar']['state'],'connected')
        self.assertEqual(server.COLLECTION_STATE['future_calendar']['monetary_policy']['state'],'failed')
        sync.assert_called_once()
        push.assert_called_once()
