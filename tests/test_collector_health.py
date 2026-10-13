from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from scripts.collector_health import evaluate, transition, atomic_json, read_json, stamp, main

NOW = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)


def sample():
    return {'canonical': {'reachable': True, 'last_clean': stamp(NOW - timedelta(minutes=10)),
             'latest_observation': stamp(NOW - timedelta(minutes=10)), 'last_detail_success': stamp(NOW - timedelta(minutes=10)),
             'runs': [{'id': 1, 'started_at': stamp(NOW.replace(hour=7, minute=17)),
                       'finished_at': stamp(NOW - timedelta(minutes=10)), 'status': 'completed', 'observations': 0, 'new_listings': 0}],
             'checks': [], 'backup': {'valid': True, 'created_at': stamp(NOW - timedelta(minutes=10))}},
            'service': {'ActiveState': 'inactive', 'Result': 'success', 'timer_active': 'active', 'timer_enabled': 'enabled'},
            'attempt': {}, 'disk': {'available': 10 * 1024**3, 'total': 20 * 1024**3},
            'memory': {'available': 1024**3, 'total': 2 * 1024**3}}


def status(value, previous=None, now=NOW):
    issues, evidence = evaluate(value, now)
    return transition(previous, issues, evidence, now)



class CollectorHealthTests(unittest.TestCase):
    def test_clean_no_new_listings_is_healthy(self):
        assert status(sample())['issues'] == {}


    def test_useful_partial_is_warning_even_with_nonzero_service_exit(self):
        value = sample()
        value['canonical']['runs'][0].update(status='partial', observations=18)
        value['service']['Result'] = 'exit-code'
        result = status(value)
        assert result['severity'] == 'warning'
        assert set(result['issues']) == {'collection_partial'}
        assert 'real observations' in result['issues']['collection_partial']['explanation']


    def test_failure_stale_and_duplicate_suppression_then_one_recovery(self):
        value = sample()
        value['canonical']['runs'][0]['status'] = 'failed'
        value['canonical']['last_clean'] = stamp(NOW - timedelta(days=3))
        value['canonical']['latest_observation'] = stamp(NOW - timedelta(days=3))
        first = status(value)
        second = status(value, first, NOW + timedelta(minutes=10))
        assert first['sequence'] == second['sequence'] == 3
        assert {e['code'] for e in first['events']} == {'clean_stale', 'observations_stale', 'collection_failed'}
        recovered = status(sample(), second)
        again = status(sample(), recovered)
        assert len([e for e in recovered['events'] if e['kind'] == 'recovered']) == 3
        assert recovered['sequence'] == again['sequence'] == 6


    def test_transport_outage_is_unknown_and_does_not_fake_recovery(self):
        value = sample()
        value['canonical']['last_clean'] = stamp(NOW - timedelta(days=3))
        first = status(value)
        value['canonical'] = {'reachable': False, 'backup': {'valid': True, 'created_at': stamp(NOW)}}
        outage = status(value, first)
        assert set(outage['issues']) == {'canonical_unreachable', 'clean_stale'}
        assert outage['evidence']['last_clean'] == first['evidence']['last_clean']
        assert not any(e['kind'] == 'recovered' for e in outage['events'])


    def test_daily_start_and_completion_grace_in_utc(self):
        value = sample()
        value['canonical']['runs'] = []
        value['canonical']['last_clean'] = stamp(NOW.replace(hour=6))
        before = NOW.replace(hour=7, minute=31)
        assert 'schedule_missed' not in status(value, now=before)['issues']
        assert 'schedule_missed' in status(value, now=before + timedelta(minutes=1))['issues']
        value = sample()
        value['service']['ActiveState'] = 'activating'
        value['canonical']['runs'][0]['status'] = 'running'
        assert 'collector_overdue' not in status(value, now=NOW.replace(hour=7, minute=56))['issues']
        assert {'collector_overdue', 'run_unfinished'} <= set(status(value)['issues'])
        # Before 07:17, use yesterday's scheduled cycle; do not raise today's missed start.
        value = sample()
        value['canonical']['runs'][0]['started_at'] = stamp(NOW.replace(hour=7, minute=17) - timedelta(days=1))
        assert 'schedule_missed' not in status(value, now=NOW.replace(hour=7, minute=16))['issues']


    def test_repeated_detail_failures_and_pauses(self):
        value = sample()
        value['canonical']['checks'] = [{'checked_at': stamp(NOW), 'outcome': 'check_failed'}] * 2
        value['canonical']['website_pause_until'] = stamp(NOW + timedelta(hours=24))
        value['canonical']['details_pause_until'] = stamp(NOW + timedelta(hours=24))
        assert {'detail_failures', 'website_paused', 'details_paused'} <= set(status(value)['issues'])
        value['canonical']['checks'] = [{'checked_at': stamp(NOW), 'outcome': 'listed'}] * 4 + value['canonical']['checks']
        assert 'detail_failures' not in status(value)['issues']


    def test_running_next_cycle_is_not_a_partial_recovery(self):
        value = sample()
        value['canonical']['runs'][0]['status'] = 'partial'
        first = status(value)
        value['canonical']['runs'].insert(0, dict(value['canonical']['runs'][0], id=2, status='running', started_at=stamp(NOW)))
        value['service']['ActiveState'] = 'activating'
        second = status(value, first)
        assert 'collection_partial' in second['issues']
        assert second['sequence'] == first['sequence']
        value['canonical']['runs'][0]['status'] = 'completed'
        value['service']['ActiveState'] = 'inactive'
        third = status(value, second)
        assert len([e for e in third['events'] if e['kind'] == 'recovered' and e['code'] == 'collection_partial']) == 1

    def test_expected_budget_and_pause_exits_are_not_transport_failures(self):
        value = sample()
        value['service'].update(Result='exit-code', ExecMainStatus='1')
        for outcome, code in [('paused', 'collection_paused'), ('budget_exhausted', 'collection_skipped')]:
            value['canonical']['runs'][0]['status'] = outcome
            result = status(value)
            assert code in result['issues']
            assert 'pipeline_failed' not in result['issues']
        value['service']['Result'] = 'timeout'
        assert 'pipeline_failed' in status(value)['issues']

    def test_systemd_start_covers_canonical_transport_outage(self):
        value = sample()
        value['canonical'] = {'reachable': False, 'backup': {'valid': True, 'created_at': stamp(NOW)}}
        value['service']['last_start'] = stamp(NOW.replace(hour=7, minute=17))
        assert 'schedule_missed' not in status(value)['issues']
        value['service']['last_start'] = stamp(NOW - timedelta(days=1))
        assert 'schedule_missed' in status(value)['issues']

    def test_backup_and_resource_severity_transition(self):
        value = sample()
        value['canonical']['backup'] = {'valid': False}
        value['disk']['available'] = 1024**3
        first = status(value)
        assert first['issues']['backup_invalid']['severity'] == 'critical'
        assert first['issues']['disk_pressure']['severity'] == 'warning'
        value['disk']['available'] = 100 * 1024**2
        second = status(value, first)
        assert second['events'][-1]['kind'] == 'severity_changed'
        value['canonical']['backup'] = {'valid': True, 'created_at': stamp(NOW - timedelta(hours=31))}
        assert 'backup_stale' in status(value)['issues']


    def test_launcher_failure_cannot_be_hidden_by_old_partial_run(self):
        value = sample()
        value['canonical']['runs'][0]['status'] = 'partial'
        value['service'].update(Result='exit-code', ExecMainStatus='1', last_start=stamp(NOW))
        assert 'pipeline_failed' in status(value)['issues']

    def test_failed_probe_does_not_claim_database_or_backup_failure(self):
        value = sample()
        value['canonical'] = {'probe_ok': False, 'reachable': False, 'backup': {'valid': None}}
        result = status(value)
        assert set(result['issues']) == {'health_probe_failed'}
        assert result['evidence']['probe_ok'] is False

    def test_installed_entrypoint_retains_issues_when_checker_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / '.collector-health/current.json'
            value = sample()
            value['canonical']['last_clean'] = stamp(NOW - timedelta(days=3))
            first = status(value)
            atomic_json(target, first)
            with patch('sys.argv', ['health', '--root', str(root)]), patch('scripts.collector_health.collect', side_effect=RuntimeError('sensitive exception')), patch('builtins.print'):
                assert main() == 0
            failed = read_json(target)
            assert {'clean_stale', 'checker_failed'} <= set(failed['issues'])
            assert 'sensitive' not in target.read_text()
            assert not any(e['kind'] == 'recovered' for e in failed['events'])
            with patch('sys.argv', ['health', '--root', str(root)]), patch('scripts.collector_health.collect', return_value=sample()), patch('builtins.print'):
                assert main() == 0
            recovered = read_json(target)
            assert len([e for e in recovered['events'] if e['code'] == 'checker_failed' and e['kind'] == 'recovered']) == 1

    def test_status_atomic_private_and_event_retention(self):
        temp = tempfile.TemporaryDirectory()
        path = Path(temp.name) / 'current.json'
        result = status(sample())
        atomic_json(path, result)
        assert read_json(path) == result
        assert path.stat().st_mode & 0o777 == 0o600
        result['sequence'] = 600
        result['events'] = [{'sequence': i} for i in range(1, 601)]
        updated = status(sample(), result)
        assert len(updated['events']) == 512
        assert updated['oldest_event_sequence'] == 89
        temp.cleanup()


if __name__ == "__main__":
    unittest.main()
