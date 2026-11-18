#!/usr/bin/env python3
"""Run slots for the twice-daily collection: which slot a run is, and whether its slot already succeeded.

Slots split the UTC day at 12:00. The morning dispatch (08:15 Europe/Sarajevo = 06:15/07:15 UTC) and its
09:40 UTC fallback fall in ``morning``; the evening dispatch (~20:15 Europe/Sarajevo = 18:15/19:15 UTC) and
its 21:40 UTC fallback fall in ``evening``. A slot is "done" when a real (non-verify) run of the workflow
succeeded inside that slot's window today, so a successful morning run never makes the evening
fallback skip, and vice versa.

  slot    --event E --reason R [--input-slot S] [--schedule CRON]      print the slot
  gate    --event E --slot S                                           print run=true|false (reads `gh run list`)
"""
import argparse
from datetime import datetime, timezone
import json
import subprocess
import sys

SLOTS = ('morning', 'evening')
SPLIT_HOUR_UTC = 12
EVENING_CRON_HOUR = '21'  # schedule fallbacks: "40 9 * * *" is morning, "40 21 * * *" is evening


def resolve_slot(event, reason='', input_slot='', schedule=''):
    """morning | evening. Explicit input wins, then the reason text, then the cron, else morning."""
    explicit = (input_slot or '').strip().lower()
    if explicit in SLOTS:
        return explicit
    if event == 'schedule':
        fields = (schedule or '').split()
        return 'evening' if len(fields) > 1 and fields[1] == EVENING_CRON_HOUR else 'morning'
    return 'evening' if 'evening' in (reason or '').lower() else 'morning'


def window_start(slot, now):
    """UTC start of the slot's window for ``now``'s day."""
    day = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    hour = SPLIT_HOUR_UTC if slot == 'evening' else 0
    return day.replace(hour=hour)


def window_end(slot, now):
    day = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return day.replace(hour=SPLIT_HOUR_UTC) if slot == 'morning' else day.replace(hour=23, minute=59, second=59)


def parse_time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def is_verify(run):
    return 'verify only' in (run.get('displayTitle') or '')


def slot_done(runs, slot, now):
    """True when a real run succeeded inside this slot's window of today (UTC)."""
    start, end = window_start(slot, now), window_end(slot, now)
    return any(r.get('conclusion') == 'success' and not is_verify(r) and start <= parse_time(r['createdAt']) <= end
               for r in runs)


def should_run(event, slot, runs, now):
    """Only the schedule fallback is ever skipped; dispatches (VPS timer or manual) always run."""
    return event != 'schedule' or not slot_done(runs, slot, now)


def gh_runs(runner=subprocess.run):
    out = runner(['gh', 'run', 'list', '--workflow', 'daily-collect.yml', '--status', 'success', '--limit', '40',
                  '--json', 'displayTitle,conclusion,createdAt'], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def main(argv=None, now=None, runs_loader=gh_runs, out=sys.stdout):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('slot')
    s.add_argument('--event', required=True)
    s.add_argument('--reason', default='')
    s.add_argument('--input-slot', default='')
    s.add_argument('--schedule', default='')
    g = sub.add_parser('gate')
    g.add_argument('--event', required=True)
    g.add_argument('--slot', required=True, choices=SLOTS)
    args = p.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    if args.command == 'slot':
        print(resolve_slot(args.event, args.reason, args.input_slot, args.schedule), file=out)
        return 0
    if args.event != 'schedule':
        print('true', file=out)
        return 0
    run = should_run(args.event, args.slot, runs_loader(), now)
    if not run:
        print(f'A {args.slot} collection already succeeded today (UTC); fallback not needed.', file=sys.stderr)
    print('true' if run else 'false', file=out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
