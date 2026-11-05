#!/usr/bin/env python3
"""Conservative website-only daily pilot. Retains observations and never infers sales."""
import argparse
from datetime import datetime, timezone
import os
from sqlalchemy.exc import SQLAlchemyError
import json
import logging
import time
import uuid

from database.db_manager import db_manager
from database.models import create_tables
from database.discovery_coverage import discovery_window, request_efficiency
from database.website_tracking import BudgetExhausted, SourcePaused, WebsiteStore, create_website_tables
from scrapers.olx_website_collector import WebsiteBlocked, WebsiteBrowser, WebsiteDOMTimeout, WebsiteReadError, parse_detail, parse_search, search_url


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sync(db, store, browser, *, search_pages=3, detail_checks=20, delay=15, brand=None, model=None,
         now=utcnow, sleep=time.sleep, session_prefix=''):
    """Browser/clock injection lets tests verify the entire run without visiting OLX."""
    query = search_url(1, brand, model)
    report = dict(status='running', search_attempted=0, detail_attempted=0, discovery_complete=0,
                  details_completed=0, observations=0, new_listings=0, errors=0,
                  scope=query, coverage='bounded_newest_sample', source_total=None, page_stats=[],
                  known_overlap=0, rejected=0, stopped_reason=None, sale_inference=False)
    session_id, started = session_prefix + str(uuid.uuid4()), now()
    run_id = store.start_run(session_id, started, query, search_pages, detail_checks)
    log_id = db.add_scraping_log(session_id=session_id, start_time=started, status='running',
                                source_query=query, pages_requested=search_pages, source_per_page=None)
    seen, fingerprints, consecutive_errors = set(), set(), 0
    validated_pages, terminal_page = 0, False
    discovery_pages, baseline = [], None

    def visit(url, kind):
        wait = store.reserve(kind, now(), search_pages, detail_checks, delay)
        report[kind + '_attempted'] += 1
        if wait:
            sleep(wait)
        return browser.read(url, kind)

    try:
        budget = store.budget(now())
        remaining_search = max(0, search_pages - budget['search_used'])
        remaining_details = max(0, detail_checks - budget['detail_used'])
        if remaining_search and not (budget['pause_until'] and budget['pause_until'] > started.isoformat() + 'Z'):
            baseline = store.discovery_baseline(query, started)
        for page_number in range(1, remaining_search + 1):
            page = parse_search(visit(search_url(page_number, brand, model), 'search'), page_number, query)
            ids = {item['listing_id'] for item in page['listings']}
            fingerprint = tuple(sorted(ids))
            if ids and fingerprint in fingerprints:
                raise WebsiteReadError('Repeated result page; discovery coverage is incomplete')
            fingerprints.add(fingerprint)
            report['source_total'] = page['source_total']
            report['rejected'] += page['rejected']
            report['page_stats'].append({k: v for k, v in page.items() if k != 'listings'})
            report['known_overlap'] += len(store.known_ids(ids - seen))
            observed_at = now()
            saved_page = {'page': page_number, 'ids': []}
            discovery_pages.append(saved_page)
            for item in page['listings']:
                if item['listing_id'] in seen:
                    saved_page['ids'].append(item['listing_id'])
                    continue
                added = db.upsert_verified_listing(item, observed_at=observed_at)
                # The upsert has committed its immutable observation already.
                # A later state-write failure must not erase it from run totals.
                report['new_listings'] += bool(added)
                report['observations'] += 1
                seen.add(item['listing_id'])
                saved_page['ids'].append(item['listing_id'])
                store.note_listed(item['listing_id'], observed_at)
            validated_pages += 1
            terminal_page = page['source_total'] == 0 or (
                page['last_page'] == page_number and len(seen) >= page['source_total'])
            if terminal_page:
                break
            if page['last_page'] == page_number and page_number < remaining_search:
                raise WebsiteReadError('Search terminal page conflicts with its result count')
        report['discovery_complete'] = int(
            remaining_search == search_pages and search_pages > 0 and report['rejected'] == 0
            and validated_pages == report['search_attempted']
            and (validated_pages == search_pages or terminal_page))
        # Search absence is deliberately unused. Only direct, successful checks affect status.
        for lid in store.candidates(now(), remaining_details):
            checked_at = now()
            try:
                result = parse_detail(visit(f'https://olx.ba/artikal/{lid}', 'detail'), lid)
                checked_at = now()
                if result['outcome'] == 'blocked':
                    raise WebsiteBlocked('Website access was refused')
                if result['outcome'] == 'listed':
                    db.upsert_verified_listing(result['listing'], observed_at=checked_at)
                    report['observations'] += 1
                store.record_check(run_id, lid, checked_at, result)
                if result['outcome'] == 'check_failed':
                    report['errors'] += 1
                    consecutive_errors += 1
                else:
                    report['details_completed'] += 1
                    consecutive_errors = 0
            except SQLAlchemyError:
                raise
            except WebsiteBlocked:
                store.record_check(run_id, lid, now(), {'outcome': 'blocked', 'evidence': {'reason': 'source_refusal'}})
                raise
            except (BudgetExhausted, SourcePaused):
                raise
            except Exception as exc:
                report['errors'] += 1
                consecutive_errors += 1
                evidence = {'reason': type(exc).__name__}
                if isinstance(exc, WebsiteDOMTimeout) and getattr(exc, 'diagnostics', None):
                    evidence['browser'] = exc.diagnostics
                store.record_check(run_id, lid, now(), {'outcome': 'check_failed',
                                   'evidence': evidence})
            if consecutive_errors >= 2:
                raise WebsiteReadError('Two consecutive detail failures; stopping without retries')
        report['status'] = ('partial' if report['errors'] or report['rejected'] else
                            'completed' if report['discovery_complete'] or report['observations'] or report['details_completed'] else 'budget_exhausted')
    except WebsiteBlocked:
        store.pause(now())
        report.update(status='paused', stopped_reason='source_refusal', errors=report['errors'] + 1)
    except SourcePaused:
        report.update(status='paused', stopped_reason='persistent_pause')
    except BudgetExhausted:
        report.update(status='budget_exhausted', stopped_reason='daily_page_budget')
    except Exception as exc:
        report.update(status='partial' if report['observations'] else 'failed',
                      stopped_reason=type(exc).__name__ if isinstance(exc, SQLAlchemyError) else str(exc)[:250], errors=report['errors'] + 1)
    finally:
        if report['status'] == 'completed' and search_pages and not report['discovery_complete']:
            report.update(status='partial', stopped_reason='discovery_budget_incomplete')
        finished = now()
        report['discovery_window'] = discovery_window(
            discovery_pages, baseline, complete=bool(report['discovery_complete']),
            terminal=terminal_page, rejected=report['rejected'], started_at=started)
        report['request_efficiency'] = request_efficiency(report, discovery_pages)
        report['availability_coverage'] = store.coverage(finished)
        store.finish_run(run_id, finished, report)
        # Failed/partial checks cannot advance the dashboard's successful-collection anchor.
        db.update_scraping_log(log_id, end_time=finished, status=report['status'] if
                              report['status'] != 'completed' or report['observations'] else 'partial',
                              errors_count=report['errors'] + report['rejected'],
                              total_listings_found=report['observations'], new_listings_added=report['new_listings'],
                              missing_from_window_count=0, error_message=report['stopped_reason'])
    return report


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--search-pages', type=int, default=3, choices=range(0, 4))
    parser.add_argument('--detail-checks', type=int, default=20, choices=range(0, 51))
    parser.add_argument('--delay', type=float, default=15)
    parser.add_argument('--brand', type=int)
    parser.add_argument('--model', type=int)
    parser.add_argument('--plan', action='store_true', help='Read budgets and due queue; no website visits')
    parser.add_argument('--setup-db', action='store_true', help='Create tables only; no website visits')
    args = parser.parse_args(argv)
    if args.delay < 15 or (args.model and not args.brand) or (not args.search_pages and not args.detail_checks):
        parser.error('Use delay >=15, a brand with a model, and at least one nonzero budget')
    return args


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO)
    if os.getenv('CAR_ANALYZER_COLLECTOR') == 'supabase':
        raise RuntimeError('Use the managed scripts/direct_collector.py entrypoint')
    if not args.plan:
        create_tables()
        create_website_tables(db_manager.engine)
    store = WebsiteStore(db_manager.engine)
    if args.setup_db:
        print('Website tracking tables ready; no source visits.')
        return 0
    if args.plan:
        print(json.dumps({'budget': store.budget(utcnow()), 'availability_coverage': store.coverage(utcnow()), 'due_sample': store.candidates(utcnow(), args.detail_checks),
                          'search_pages': args.search_pages, 'detail_checks': args.detail_checks,
                          'delay_seconds': args.delay, 'coverage': 'bounded_newest_sample'}, indent=2))
        return 0
    with store.lock():
        # Do not even launch Chromium during a persistent refusal pause or an exhausted day.
        budget = store.budget(utcnow())
        if budget['pause_until'] and budget['pause_until'] > utcnow().isoformat() + 'Z':
            print(json.dumps({'status': 'paused', **budget}))
            return 1
        if budget['search_used'] >= args.search_pages and budget['detail_used'] >= args.detail_checks:
            print(json.dumps({'status': 'budget_exhausted', **budget}))
            return 1
        with WebsiteBrowser() as browser:
            report = sync(db_manager, store, browser, search_pages=args.search_pages,
                          detail_checks=args.detail_checks, delay=args.delay, brand=args.brand, model=args.model)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
