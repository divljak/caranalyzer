"""Evidence about a bounded discovery window, never a market-completeness claim."""
from datetime import datetime

VERSION = 'discovery_window_v1'


def discovery_window(pages, baseline, *, complete, terminal, rejected, started_at):
    """Compare saved source identities, not renewed dates or all historical IDs.

    Two consecutive substantial pages with >=80% prior-window identities are an
    overlap signal only. Moving ranks/promotions can still hide unseen ads. It
    never authorizes extra requests or early stopping on one familiar listing.
    """
    prior = set((baseline or {}).get('ids', []))
    previous = None
    page_metrics, ids, substantial_overlap_pages = [], set(), []
    for page in pages:
        current = set(page['ids'])
        overlap = current & prior
        strong = len(current) >= 5 and len(overlap) / len(current) >= .8
        substantial_overlap_pages.append(strong)
        page_metrics.append({'page': page['page'], 'saved_ids': len(current),
                             'prior_window_ids': len(overlap),
                             'prior_window_fraction': round(len(overlap) / len(current), 4) if current else None})
        ids.update(current)
    if baseline:
        previous = baseline['finished_at']
        if isinstance(previous, str):
            previous = datetime.fromisoformat(previous.rstrip('Z'))
    age_hours = (started_at - previous).total_seconds() / 3600 if previous else None
    overlap_signal = any(a and b for a, b in zip(substantial_overlap_pages, substantial_overlap_pages[1:]))
    if not pages:
        status = 'not_observed'
    elif not complete or rejected:
        status = 'incomplete'
    elif terminal:
        status = 'current_results_exhausted'
    elif not baseline:
        status = 'baseline_unavailable'
    elif age_hours > 48:
        status = 'possible_gap_stale_baseline'
    elif overlap_signal:
        status = 'overlap_observed'
    else:
        status = 'possible_gap'
    return {'version': VERSION, 'status': status,
            'baseline_run_id': (baseline or {}).get('run_id'),
            'baseline_finished_at': previous.isoformat() + 'Z' if previous else None,
            'baseline_age_hours': round(age_hours, 2) if age_hours is not None else None,
            'baseline_ids': len(prior), 'distinct_saved_ids': len(ids),
            'prior_window_ids_seen': len(ids & prior), 'ids_not_in_prior_window': len(ids - prior),
            'overlap_signal': overlap_signal, 'pages': page_metrics,
            'full_market_coverage': 'unknown', 'missed_listing_count': None,
            'ids': sorted(ids)}


def request_efficiency(report, pages):
    """Committed discovery evidence and reserved visits have separate denominators."""
    discovery = len({lid for p in pages for lid in p['ids']})
    searches, details = report['search_attempted'], report['detail_attempted']
    return {'search_visits': searches, 'detail_visits': details,
            'saved_discovery_observations': discovery,
            'saved_direct_observations': report['observations'] - discovery,
            'new_database_ids': report['new_listings'],
            'discovery_observations_per_search_visit': round(discovery / searches, 2) if searches else None,
            'new_ids_per_search_visit': round(report['new_listings'] / searches, 2) if searches else None,
            'completed_detail_checks': report['details_completed'],
            'detail_completion_fraction': round(report['details_completed'] / details, 4) if details else None,
            'complete_new_listing_coverage': 'unverified'}
