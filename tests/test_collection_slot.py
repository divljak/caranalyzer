"""Slot resolution and the slot-aware 'already succeeded' gate, the health probe and the stale alert."""
from datetime import datetime, timedelta, timezone
import io
import unittest

from scripts import collection_alert as ca
from scripts import collection_slot as cs
from scripts.collector_health import evaluate

UTC = timezone.utc


def run(day, hour, minute=0, conclusion='success', title='Daily collection (vps-timer)'):
    return {'createdAt': datetime(2026, 11, day, hour, minute, tzinfo=UTC).isoformat(), 'conclusion': conclusion,
            'displayTitle': title, 'status': 'completed', 'url': 'https://x/run'}


class ResolveTests(unittest.TestCase):
    def test_slot_from_input_reason_and_cron(self):
        r = cs.resolve_slot
        self.assertEqual('evening', r('workflow_dispatch', 'vps-timer-evening'))
        self.assertEqual('morning', r('workflow_dispatch', 'vps-timer'))
        self.assertEqual('morning', r('workflow_dispatch', 'manual'))
        self.assertEqual('evening', r('workflow_dispatch', 'vps-timer', input_slot='evening'))
        self.assertEqual('morning', r('workflow_dispatch', 'vps-timer-evening', input_slot='morning'))
        self.assertEqual('morning', r('workflow_dispatch', '', input_slot='auto'))
        self.assertEqual('morning', r('schedule', schedule='40 9 * * *'))
        self.assertEqual('evening', r('schedule', schedule='40 21 * * *'))


class GateTests(unittest.TestCase):
    NOON = datetime(2026, 11, 18, 21, 41, tzinfo=UTC)

    def test_morning_success_does_not_skip_the_evening_fallback(self):
        runs = [run(18, 7, 15)]
        self.assertFalse(cs.should_run('schedule', 'morning', runs, datetime(2026, 11, 18, 9, 40, tzinfo=UTC)))
        self.assertTrue(cs.should_run('schedule', 'evening', runs, self.NOON))

    def test_evening_success_skips_only_the_evening_fallback(self):
        runs = [run(18, 19, 15, title='Daily collection (vps-timer-evening)')]
        self.assertFalse(cs.should_run('schedule', 'evening', runs, self.NOON))

    def test_failed_verify_old_or_other_day_runs_do_not_count(self):
        runs = [run(18, 19, 15, conclusion='failure'), run(18, 19, 20, title='Daily collection (check, verify only)'),
                run(17, 19, 15), run(18, 11, 59)]
        self.assertTrue(cs.should_run('schedule', 'evening', runs, self.NOON))

    def test_morning_fallback_still_skips_after_a_morning_success_and_runs_without_one(self):
        now = datetime(2026, 11, 18, 9, 40, tzinfo=UTC)
        self.assertFalse(cs.should_run('schedule', 'morning', [run(18, 6, 15)], now))
        self.assertTrue(cs.should_run('schedule', 'morning', [run(17, 19, 15)], now))

    def test_dispatches_are_never_skipped(self):
        runs = [run(18, 7, 15), run(18, 19, 15)]
        self.assertTrue(cs.should_run('workflow_dispatch', 'evening', runs, self.NOON))

    def test_cli_prints_true_false(self):
        out = io.StringIO()
        cs.main(['gate', '--event', 'schedule', '--slot', 'evening'], now=self.NOON, runs_loader=lambda: [run(18, 19, 15)], out=out)
        self.assertEqual('false', out.getvalue().strip())
        out = io.StringIO()
        cs.main(['slot', '--event', 'workflow_dispatch', '--reason', 'vps-timer-evening'], out=out)
        self.assertEqual('evening', out.getvalue().strip())


class AlertTests(unittest.TestCase):
    def test_stale_slot_check_catches_a_missing_evening_run_that_26h_would_hide(self):
        now = datetime(2026, 11, 18, 23, 15, tzinfo=UTC)
        runs = [run(18, 7, 15)]
        self.assertIsNone(ca.evaluate_runs(runs, now))  # 26 h rule: morning run hides the gap
        verdict = ca.evaluate_runs(runs, now, slot='evening')
        self.assertIsNotNone(verdict)
        self.assertIn('evening', ca.build_message('stale', verdict['reason'], verdict['url'], slot='evening'))
        self.assertIsNone(ca.evaluate_runs(runs + [run(18, 19, 15)], now, slot='evening'))
        self.assertIsNone(ca.evaluate_runs(runs, datetime(2026, 11, 18, 10, 30, tzinfo=UTC), slot='morning'))


class HealthTests(unittest.TestCase):
    def sample(self, starts):
        runs = [{'status': 'completed', 'started_at': t.isoformat(), 'observations': 5, 'errors': 0} for t in starts]
        return {'schedule': 'actions', 'service': {}, 'attempt': {}, 'disk': {'available': 10**11, 'total': 10**12},
                'memory': {'available': 10**9, 'total': 2 * 10**9},
                'canonical': {'reachable': True, 'probe_ok': True, 'runs': runs, 'checks': [],
                              'last_clean': (starts[0] if starts else datetime(2026, 11, 1, tzinfo=UTC)).isoformat(),
                              'latest_observation': (starts[0] if starts else datetime(2026, 11, 1, tzinfo=UTC)).isoformat()}}

    def missed(self, now, starts):
        issues, summary = evaluate(self.sample(sorted(starts, reverse=True)), now)
        return 'schedule_missed' in issues, summary

    def test_missing_evening_run_is_flagged_after_the_evening_fallback_and_grace(self):
        morning = datetime(2026, 11, 18, 7, 15, tzinfo=UTC)
        self.assertFalse(self.missed(datetime(2026, 11, 18, 21, 30, tzinfo=UTC), [morning])[0])  # nothing due yet
        self.assertFalse(self.missed(datetime(2026, 11, 18, 22, 30, tzinfo=UTC), [morning])[0])  # inside the grace
        flagged, summary = self.missed(datetime(2026, 11, 18, 23, 0, tzinfo=UTC), [morning])
        self.assertTrue(flagged)
        self.assertTrue(summary['expected_start'].startswith('2026-11-18T21:40'))

    def test_evening_run_clears_it_and_morning_judging_is_unchanged(self):
        morning, evening = datetime(2026, 11, 18, 7, 15, tzinfo=UTC), datetime(2026, 11, 18, 19, 15, tzinfo=UTC)
        self.assertFalse(self.missed(datetime(2026, 11, 18, 23, 0, tzinfo=UTC), [morning, evening])[0])
        self.assertTrue(self.missed(datetime(2026, 11, 18, 11, 0, tzinfo=UTC), [evening - timedelta(days=1)])[0])
        self.assertFalse(self.missed(datetime(2026, 11, 18, 11, 0, tzinfo=UTC), [morning])[0])


if __name__ == '__main__':
    unittest.main()
