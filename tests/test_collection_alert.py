from datetime import datetime, timedelta, timezone
import io
import unittest
from contextlib import redirect_stdout, redirect_stderr

from scripts import collection_alert as ca

NOW = datetime(2026, 11, 17, 10, 30, tzinfo=timezone.utc)


def run(hours_ago, conclusion='success', title='Daily collection (VPS)', status='completed', url='https://x/run/1'):
    return {'createdAt': (NOW - timedelta(hours=hours_ago)).isoformat(), 'conclusion': conclusion,
            'displayTitle': title, 'status': status, 'url': url}


class EvaluateRuns(unittest.TestCase):
    def test_recent_success_is_healthy(self):
        self.assertIsNone(ca.evaluate_runs([run(2)], NOW))

    def test_old_success_is_stale(self):
        self.assertIn('no daily collection run', ca.evaluate_runs([run(30)], NOW)['reason'])

    def test_verify_success_does_not_count(self):
        v = run(1, title='Daily collection (manual, verify only)')
        self.assertIsNotNone(ca.evaluate_runs([v], NOW))

    def test_latest_failure_is_reported_with_link(self):
        verdict = ca.evaluate_runs([run(3, 'failure', url='https://x/run/9'), run(30)], NOW)
        self.assertIn('failure', verdict['reason'])
        self.assertEqual(verdict['url'], 'https://x/run/9')

    def test_in_progress_run_reported(self):
        verdict = ca.evaluate_runs([run(1, conclusion='', status='in_progress')], NOW)
        self.assertIn('in_progress', verdict['reason'])

    def test_empty(self):
        self.assertIsNotNone(ca.evaluate_runs([], NOW))


class Main(unittest.TestCase):
    ENV = {'TELEGRAM_BOT_TOKEN': 'SECRET-TOKEN', 'TELEGRAM_CHAT_ID': '-100, -200'}

    def call(self, argv, env=None, runs=(), sender=None):
        sent = []
        out, err = io.StringIO(), io.StringIO()
        def fake(token, chat, text):
            sent.append((token, chat, text)); return True
        with redirect_stdout(out), redirect_stderr(err):
            code = ca.main(argv, env if env is not None else self.ENV, NOW, lambda: list(runs), sender or fake)
        return code, sent, out.getvalue() + err.getvalue()

    def test_failure_sends_to_first_chat_with_link(self):
        code, sent, _ = self.call(['failure', '--run-url', 'https://x/run/5'])
        self.assertEqual(code, 0)
        self.assertEqual(sent[0][:2], ('SECRET-TOKEN', '-100'))
        self.assertIn('FAILED', sent[0][2]); self.assertIn('https://x/run/5', sent[0][2])

    def test_dry_run_sends_nothing_and_needs_no_secrets(self):
        code, sent, out = self.call(['failure', '--run-url', 'u', '--dry-run'], env={})
        self.assertEqual((code, sent), (0, []))
        self.assertIn('DRY RUN', out)

    def test_missing_secrets_named_not_valued(self):
        code, sent, out = self.call(['failure'], env={'TELEGRAM_BOT_TOKEN': 'SECRET-TOKEN'})
        self.assertEqual(code, 2); self.assertIn('TELEGRAM_CHAT_ID', out); self.assertNotIn('SECRET-TOKEN', out)

    def test_stale_healthy_sends_nothing(self):
        code, sent, _ = self.call(['stale'], runs=[run(2)])
        self.assertEqual((code, sent), (0, []))

    def test_stale_alerts(self):
        code, sent, _ = self.call(['stale'], runs=[run(30)])
        self.assertEqual(code, 0); self.assertIn('no successful daily collection', sent[0][2])

    def test_send_error_never_leaks_token(self):
        def boom(*a): raise RuntimeError('https://api.telegram.org/botSECRET-TOKEN/x')
        code, _, out = self.call(['failure'], sender=boom)
        self.assertEqual(code, 1); self.assertNotIn('SECRET-TOKEN', out)


if __name__ == '__main__':
    unittest.main()
