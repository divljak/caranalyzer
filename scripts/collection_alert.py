#!/usr/bin/env python3
"""Telegram alerts for the daily GitHub Actions collection (stdlib only).

  failure  --run-url URL [--reason TEXT]   a daily-collect run failed
  stale    [--max-age-hours 26]            no successful daily run in the window (reads `gh run list`)

Secrets come from the environment, the same names the bot/digest use: TELEGRAM_BOT_TOKEN and
TELEGRAM_CHAT_ID (first id when several are comma-separated). --dry-run prints the text and never
touches the network. Nothing secret is ever printed.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root, so this runs as a script too
from scripts.collection_slot import SLOTS, window_start  # noqa: E402

WORKFLOW = 'daily-collect.yml'
REQUIRED = ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID')


def parse_time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def is_verify(run):
    return 'verify only' in (run.get('displayTitle') or '')


def evaluate_runs(runs, now, max_age=timedelta(hours=26), slot=None):
    """Return None when a real (non-verify) collection succeeded in the window, else an alert dict.

    With ``slot`` (morning | evening) the window is that slot's part of today (UTC) instead of the last
    ``max_age`` hours, so a good morning run cannot hide a missing evening run (see collection_slot.py)."""
    real = [r for r in runs if not is_verify(r)]
    cutoff = now - max_age if slot is None else window_start(slot, now)
    if any(r.get('conclusion') == 'success' and parse_time(r['createdAt']) >= cutoff for r in real):
        return None
    recent = sorted((r for r in real if parse_time(r['createdAt']) >= cutoff),
                    key=lambda r: r['createdAt'], reverse=True)
    if not recent:
        return {'reason': 'no daily collection run started', 'url': None}
    last = recent[0]
    state = last.get('conclusion') or last.get('status') or 'unknown'
    return {'reason': f'last run did not succeed ({state})', 'url': last.get('url')}


def build_message(kind, reason, url, hours=26, slot=None):
    if kind == 'failure':
        head = 'Car Analyzer: daily collection FAILED'
    elif slot:
        head = f'Car Analyzer: no successful {slot} collection today (UTC)'
    else:
        head = f'Car Analyzer: no successful daily collection in the last {hours}h'
    lines = [head, reason]
    lines.append(url if url else 'No run found. Check the workflow: Actions > Daily collection')
    return '\n'.join(lines)


def telegram_config(environ):
    token = (environ.get('TELEGRAM_BOT_TOKEN') or '').strip()
    chat = (environ.get('TELEGRAM_CHAT_ID') or '').strip().split(',')[0].strip()
    missing = [n for n, v in (('TELEGRAM_BOT_TOKEN', token), ('TELEGRAM_CHAT_ID', chat)) if not v]
    return token, chat, missing


def send(token, chat, text, opener=urllib.request.urlopen):
    data = urllib.parse.urlencode({'chat_id': chat, 'text': text, 'disable_web_page_preview': 'true'}).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=data)
    with opener(req, timeout=20) as response:
        return 200 <= response.status < 300


def gh_runs(runner=subprocess.run):
    out = runner(['gh', 'run', 'list', '--workflow', WORKFLOW, '--limit', '30', '--json',
                  'databaseId,displayTitle,status,conclusion,createdAt,url'],
                 check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def main(argv=None, environ=None, now=None, runs_loader=gh_runs, sender=send):
    environ = os.environ if environ is None else environ
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('kind', choices=('failure', 'stale'))
    p.add_argument('--run-url')
    p.add_argument('--reason', default='a step failed; open the run for the log')
    p.add_argument('--max-age-hours', type=int, default=26)
    p.add_argument('--slot', choices=SLOTS, help='stale: judge only this slot of today (UTC) instead of the last N hours')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    if args.kind == 'failure':
        reason, url = args.reason, args.run_url
    else:
        verdict = evaluate_runs(runs_loader(), now, timedelta(hours=args.max_age_hours), args.slot)
        if verdict is None:
            print('Healthy: a daily collection succeeded in the window; no alert.')
            return 0
        reason, url = verdict['reason'], verdict['url']
    text = build_message(args.kind, reason, url, args.max_age_hours, args.slot if args.kind == 'stale' else None)
    if args.dry_run:
        print('DRY RUN, not sent. Message:\n' + text)
        return 0
    token, chat, missing = telegram_config(environ)
    if missing:
        print('Cannot alert, missing GitHub secret(s): ' + ', '.join(missing), file=sys.stderr)
        return 2
    try:
        ok = sender(token, chat, text)
    except Exception as exc:  # never echo the exception text: the URL contains the token
        print(f'Telegram send failed ({type(exc).__name__})', file=sys.stderr)
        return 1
    print('Alert sent.' if ok else 'Telegram rejected the alert.')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
