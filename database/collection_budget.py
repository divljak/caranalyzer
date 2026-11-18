"""Per-run and per-UTC-day source budgets for the twice-daily collection (pure, no I/O).

Approved plan (18 Nov 2026): two runs a day about 12 h apart, 3 search pages each (6 a day),
20 detail checks a day in total, at least 15 s between visits, and a hard ceiling of 10 search
pages and 50 detail checks a day that no setting can exceed.

* Search: every run may read up to ``search_per_run`` pages, and the day stops at ``search_day_cap``.
  A failed morning run therefore never takes the evening run's pages (3 + 3 = 6).
* Details: the morning run gets ``detail_morning`` checks (10). The evening run gets whatever is
  left of the day (10 normally, more when the morning run did little), never above the day cap.
* A run outside the two named slots (manual, ``auto``) may use the rest of the day within the
  per-run limits, so a manual run cannot push the day past its caps.
"""
import os

HARD_MAX_SEARCH_PER_DAY = 10
HARD_MAX_DETAIL_PER_DAY = 50
MIN_SPACING_SECONDS = 15
SLOTS = ('morning', 'evening')

DEFAULT_SEARCH_PER_RUN = 3
DEFAULT_SEARCH_DAY_CAP = 6
DEFAULT_DETAIL_DAY_CAP = 20
DEFAULT_DETAIL_MORNING = 10


def _int(environ, name, default):
    raw = (environ.get(name) or '').strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f'{name} must be a whole number')


def config_from_env(environ=None):
    """Budget settings; each can be lowered or raised by env, never past the hard maximums."""
    environ = os.environ if environ is None else environ
    cfg = dict(
        search_per_run=_int(environ, 'COLLECTOR_SEARCH_PAGES_PER_RUN', DEFAULT_SEARCH_PER_RUN),
        search_day_cap=_int(environ, 'COLLECTOR_SEARCH_DAY_CAP', DEFAULT_SEARCH_DAY_CAP),
        detail_day_cap=_int(environ, 'COLLECTOR_DETAIL_DAY_CAP', DEFAULT_DETAIL_DAY_CAP),
        detail_morning=_int(environ, 'COLLECTOR_DETAIL_MORNING', DEFAULT_DETAIL_MORNING),
    )
    return validate(cfg)


def validate(cfg):
    if not 0 <= cfg['search_day_cap'] <= HARD_MAX_SEARCH_PER_DAY:
        raise ValueError(f'search day cap must be 0-{HARD_MAX_SEARCH_PER_DAY}')
    if not 0 <= cfg['detail_day_cap'] <= HARD_MAX_DETAIL_PER_DAY:
        raise ValueError(f'detail day cap must be 0-{HARD_MAX_DETAIL_PER_DAY}')
    if not 0 <= cfg['search_per_run'] <= cfg['search_day_cap']:
        raise ValueError('search pages per run must be between 0 and the search day cap')
    if not 0 <= cfg['detail_morning'] <= cfg['detail_day_cap']:
        raise ValueError('morning detail checks must be between 0 and the detail day cap')
    return cfg


def normalize_slot(value):
    value = (value or '').strip().lower()
    if value in ('', 'auto'):
        return 'auto'
    if value not in SLOTS:
        raise ValueError(f'slot must be one of auto, {", ".join(SLOTS)}')
    return value


def plan_run(slot, budget, cfg):
    """What this run may spend, given the day's usage so far (``store.budget()``).

    Returns per-run ``search_pages`` / ``detail_checks`` plus the day caps and what remains.
    """
    cfg = validate(dict(cfg))
    slot = normalize_slot(slot)
    search_left = max(0, cfg['search_day_cap'] - budget['search_used'])
    detail_left = max(0, cfg['detail_day_cap'] - budget['detail_used'])
    search_pages = min(cfg['search_per_run'], search_left)
    if slot == 'morning':
        detail_checks = min(cfg['detail_morning'], detail_left)
    else:  # evening and auto: the leftover of the day
        detail_checks = detail_left
    return dict(slot=slot, search_pages=search_pages, detail_checks=detail_checks,
                search_day_cap=cfg['search_day_cap'], detail_day_cap=cfg['detail_day_cap'],
                search_left=search_left, detail_left=detail_left,
                exhausted=search_pages <= 0 and detail_checks <= 0)
