"""Persistent page budgets and direct-ad evidence; search absence never means sold."""
from datetime import timedelta
import json
from contextlib import contextmanager

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.orm import sessionmaker

from database.models import Base, CarListing, ListingSnapshot


class WebsiteBudget(Base):
    __tablename__ = 'website_collection_budget'
    id = Column(Integer, primary_key=True)
    day = Column(Date, nullable=False)
    search_visits = Column(Integer, nullable=False, default=0)
    detail_visits = Column(Integer, nullable=False, default=0)
    last_visit_at = Column(DateTime)
    pause_until = Column(DateTime)


class WebsiteRun(Base):
    __tablename__ = 'website_collection_runs'
    id = Column(Integer, primary_key=True)
    session_id = Column(String(50), nullable=False, unique=True)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime)
    status = Column(String(30), nullable=False, default='running')
    source_query = Column(String(500), nullable=False)
    search_limit = Column(Integer, nullable=False)
    detail_limit = Column(Integer, nullable=False)
    search_attempted = Column(Integer, nullable=False, default=0)
    detail_attempted = Column(Integer, nullable=False, default=0)
    discovery_complete = Column(Integer, nullable=False, default=0)
    details_completed = Column(Integer, nullable=False, default=0)
    observations = Column(Integer, nullable=False, default=0)
    new_listings = Column(Integer, nullable=False, default=0)
    errors = Column(Integer, nullable=False, default=0)
    report = Column(Text)


class WebsiteListingState(Base):
    __tablename__ = 'website_listing_states'
    listing_id = Column(String(50), ForeignKey('car_listings.listing_id', ondelete='RESTRICT'), primary_key=True)
    status = Column(String(40), nullable=False, default='unknown')
    last_checked_at = Column(DateTime)
    last_success_at = Column(DateTime)
    last_listed_at = Column(DateTime)
    last_outcome = Column(String(40))
    unavailable_since = Column(DateTime)
    unavailable_checks = Column(Integer, nullable=False, default=0)
    next_check_at = Column(DateTime)
    details_checked_at = Column(DateTime)
    transmission = Column(String(20))
    attributes = Column(Text)
    published_date = Column(Date)
    renewed_at = Column(DateTime)  # UTC; the displayed Sarajevo timestamp is converted explicitly


class WebsiteListingCheck(Base):
    __tablename__ = 'website_listing_checks'
    id = Column(Integer, primary_key=True)
    listing_id = Column(String(50), ForeignKey('car_listings.listing_id', ondelete='RESTRICT'), nullable=False, index=True)
    run_id = Column(Integer, ForeignKey('website_collection_runs.id', ondelete='RESTRICT'), nullable=False)
    checked_at = Column(DateTime, nullable=False)
    outcome = Column(String(40), nullable=False)
    source_url = Column(String(500), nullable=False)
    evidence = Column(Text, nullable=False)


WEBSITE_TABLES = (WebsiteBudget.__table__, WebsiteRun.__table__, WebsiteListingState.__table__, WebsiteListingCheck.__table__)


def create_website_tables(engine):
    Base.metadata.create_all(engine, tables=list(WEBSITE_TABLES))


class BudgetExhausted(RuntimeError):
    pass


class SourcePaused(RuntimeError):
    pass


class WebsiteStore:
    def __init__(self, engine):
        self.engine = engine
        self.sessions = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def lock(self):
        """Hold a PostgreSQL session lock for the entire run, across transactions."""
        if self.engine.dialect.name != 'postgresql':
            yield
            return
        with self.engine.connect() as connection:
            if not connection.execute(text('SELECT pg_try_advisory_lock(731400220)')).scalar():
                raise RuntimeError('Another website sync is running')
            try:
                yield
            finally:
                connection.execute(text('SELECT pg_advisory_unlock(731400220)'))

    def budget(self, now):
        with self.sessions() as session:
            row = session.get(WebsiteBudget, 1)
            return {'day': now.date().isoformat(),
                    'search_used': row.search_visits if row and row.day == now.date() else 0,
                    'detail_used': row.detail_visits if row and row.day == now.date() else 0,
                    'pause_until': row.pause_until.isoformat() + 'Z' if row and row.pause_until else None}

    def reserve(self, kind, now, search_limit, detail_limit, min_spacing):
        """Count attempts BEFORE navigation; failed visits and reruns consume budget."""
        if kind not in {'search', 'detail'}:
            raise ValueError('Unknown visit type')
        if not 0 <= search_limit <= 3 or not 0 <= detail_limit <= 50 or min_spacing < 15:
            raise ValueError('Pilot ceilings are 3 search pages, 50 ad pages, and at least 15 seconds between visits')
        with self.sessions.begin() as session:
            row = session.query(WebsiteBudget).filter_by(id=1).with_for_update().first()
            if row is None:
                row = WebsiteBudget(id=1, day=now.date(), search_visits=0, detail_visits=0)
                session.add(row)
            if row.pause_until and row.pause_until > now:
                raise SourcePaused('Website checks are paused after a refusal')
            if row.day != now.date():
                row.day, row.search_visits, row.detail_visits = now.date(), 0, 0
            field, limit = ('search_visits', search_limit) if kind == 'search' else ('detail_visits', detail_limit)
            if getattr(row, field) >= limit:
                raise BudgetExhausted(f'Daily {kind} page budget is exhausted')
            wait = max(0, min_spacing - (now - row.last_visit_at).total_seconds()) if row.last_visit_at else 0
            setattr(row, field, getattr(row, field) + 1)
            row.last_visit_at = now + timedelta(seconds=wait)
            return wait

    def pause(self, now, hours=24):
        with self.sessions.begin() as session:
            row = session.get(WebsiteBudget, 1)
            if row is None:
                row = WebsiteBudget(id=1, day=now.date(), search_visits=0, detail_visits=0)
                session.add(row)
            until = now + timedelta(hours=hours)
            row.pause_until = max(row.pause_until or until, until)

    def start_run(self, session_id, now, query, search_limit, detail_limit):
        with self.sessions.begin() as session:
            row = WebsiteRun(session_id=session_id, started_at=now, source_query=query,
                             search_limit=search_limit, detail_limit=detail_limit)
            session.add(row)
            session.flush()
            return row.id

    def finish_run(self, run_id, now, report):
        with self.sessions.begin() as session:
            row = session.get(WebsiteRun, run_id)
            row.finished_at = now
            for name in ['status', 'search_attempted', 'detail_attempted', 'discovery_complete',
                         'details_completed', 'observations', 'new_listings', 'errors']:
                setattr(row, name, report[name])
            row.report = json.dumps(report, ensure_ascii=False)

    def known_ids(self, ids):
        if not ids:
            return set()
        with self.sessions() as session:
            return {row[0] for row in session.query(CarListing.listing_id).filter(CarListing.listing_id.in_(ids))}

    def discovery_baseline(self, query, before):
        """Read the last validated same-scope discovery, including partial detail runs.

        Reconstruct from immutable observations, so pre-instrumentation runs are
        usable too. The interval attribution relies on the collector's exclusive
        advisory lock; current/manual/failed discovery rows are never baselines.
        """
        with self.sessions() as session:
            run = session.query(WebsiteRun).filter(
                WebsiteRun.source_query == query, WebsiteRun.discovery_complete == 1,
                WebsiteRun.finished_at.isnot(None), WebsiteRun.finished_at <= before,
                WebsiteRun.status.in_(['completed', 'partial'])).order_by(
                    WebsiteRun.started_at.desc(), WebsiteRun.id.desc()).first()
            if run is None:
                return None
            rows = session.query(ListingSnapshot.listing_id).filter(
                ListingSnapshot.source_query == query, ListingSnapshot.source_page.isnot(None),
                ListingSnapshot.observed_at >= run.started_at,
                ListingSnapshot.observed_at <= run.finished_at).distinct().limit(1001).all()
            # Refuse an unbounded or empty historical extract as a useful overlap baseline.
            if not rows or len(rows) > 1000:
                return None
            return {'run_id': run.id, 'finished_at': run.finished_at,
                    'ids': sorted(row[0] for row in rows)}

    def coverage(self, now):
        with self.sessions() as session:
            base = session.query(CarListing).filter(CarListing.source == 'olx.ba',
                CarListing.collection_method.in_(['olx_api', 'olx_website']))
            tracked = base.count()
            checked = base.join(WebsiteListingState).filter(
                WebsiteListingState.last_success_at >= now - timedelta(days=7)).count()
            due = base.outerjoin(WebsiteListingState).filter(or_due(WebsiteListingState.next_check_at, now)).count()
            return {'tracked_listings': tracked, 'successfully_checked_last_7_days': checked,
                    'due_for_check': due, 'source_coverage': 'unknown'}

    def note_listed(self, listing_id, now):
        with self.sessions.begin() as session:
            row = session.get(WebsiteListingState, listing_id)
            if row is None:
                row = WebsiteListingState(listing_id=listing_id)
                session.add(row)
            row.status, row.last_listed_at = 'listed', now
            row.unavailable_since, row.unavailable_checks = None, 0
            # Search sightings restore availability, but do not refresh detail-check times.
            if row.next_check_at is None:
                row.next_check_at = now

    def candidates(self, now, limit, excluded=()):
        """Prioritize rechecks; split remaining capacity between new and stale ads."""
        if not limit:
            return []
        with self.sessions() as session:
            base = session.query(CarListing.listing_id).outerjoin(WebsiteListingState).filter(
                CarListing.source == 'olx.ba', CarListing.collection_method.in_(['olx_api', 'olx_website']),
                or_due(WebsiteListingState.next_check_at, now))
            excluded = set(excluded)
            if excluded:
                base = base.filter(~CarListing.listing_id.in_(excluded))
            pending = base.filter(WebsiteListingState.status == 'unavailable_pending').order_by(
                WebsiteListingState.next_check_at, CarListing.listing_id).limit(limit).all()
            selected = [r[0] for r in pending if r[0] not in excluded]
            remaining = limit - len(selected)
            if remaining:
                recent = base.filter(~CarListing.listing_id.in_(excluded | set(selected)),
                                     WebsiteListingState.last_checked_at.is_(None)).order_by(
                    CarListing.first_seen_at.desc(), CarListing.listing_id).limit((remaining + 1) // 2).all()
                selected.extend(r[0] for r in recent)
            if len(selected) < limit:
                stale = base.filter(~CarListing.listing_id.in_(excluded | set(selected))).order_by(
                    func.coalesce(WebsiteListingState.last_checked_at, CarListing.last_verified_at),
                    CarListing.listing_id).limit(limit - len(selected)).all()
                selected.extend(r[0] for r in stale)
            return [lid for lid in selected if lid.isdigit()]

    def record_check(self, run_id, listing_id, now, result):
        outcome = result['outcome']
        if outcome not in {'listed', 'unavailable', 'source_reported_ended', 'source_reported_sold', 'check_failed', 'blocked'}:
            raise ValueError('Unsupported direct-check outcome')
        with self.sessions.begin() as session:
            row = session.get(WebsiteListingState, listing_id)
            if row is None:
                row = WebsiteListingState(listing_id=listing_id, status='unknown', unavailable_checks=0)
                session.add(row)
            session.add(WebsiteListingCheck(listing_id=listing_id, run_id=run_id, checked_at=now,
                        outcome=outcome, source_url=f'https://olx.ba/artikal/{listing_id}',
                        evidence=json.dumps(result.get('evidence', {}), ensure_ascii=False)))
            row.last_checked_at, row.last_outcome = now, outcome
            row.next_check_at = now + timedelta(hours=24)
            listing = session.get(CarListing, listing_id)
            if outcome in {'check_failed', 'blocked'}:
                return  # No negative evidence, no clearing previous attributes or status.
            row.last_success_at = now
            if outcome == 'listed':
                row.status, row.last_listed_at = 'listed', now
                row.unavailable_since, row.unavailable_checks = None, 0
                listing.is_active = True
                data = result.get('listing', {})
                if data:
                    attrs = result.get('evidence', {}).get('attributes', {})
                    row.attributes = json.dumps(attrs, ensure_ascii=False)
                    row.details_checked_at = now
                    row.transmission = data.get('transmission')
                    row.published_date = data.get('posted_date')
                    row.renewed_at = result.get('renewed_at')
            elif outcome == 'unavailable':
                if row.unavailable_since is None:
                    row.unavailable_since = now
                    row.unavailable_checks = 1
                else:
                    row.unavailable_checks = (row.unavailable_checks or 0) + 1
                age = now - row.unavailable_since
                persistent = row.unavailable_checks >= 3 and age >= timedelta(hours=72)
                row.status = 'removed_unconfirmed' if persistent else 'unavailable_pending'
                row.next_check_at = now + (timedelta(days=7) if persistent else
                                           max(timedelta(hours=24), timedelta(hours=72) - age) if row.unavailable_checks >= 2 else timedelta(hours=24))
                if persistent:
                    listing.is_active = False  # Verified persistent absence; sale reason remains unknown.
            else:
                row.status = outcome
                row.next_check_at = now + timedelta(days=7)
                listing.is_active = False


def or_due(column, now):
    from sqlalchemy import or_
    return or_(column.is_(None), column <= now)
