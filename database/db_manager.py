"""
Database manager for OLX Car Scraper
Handles all database operations including CRUD operations
"""
import sys
import os
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, and_, or_
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any
import logging

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import CarListing, ListingSnapshot, ScrapingLog, create_engine_and_session
from config.settings import VALIDATION

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DatabaseManager:
    """Manages all database operations for the OLX car scraper"""
    
    def __init__(self):
        self.engine, self.SessionLocal = create_engine_and_session()
    
    def get_session(self) -> Session:
        """Get a new database session"""
        return self.SessionLocal()
    
    def add_or_update_listing(self, listing_data: Dict[str, Any]) -> bool:
        """Add a new listing or update existing one"""
        session = self.get_session()
        try:
            existing_listing = session.query(CarListing).filter(
                CarListing.listing_id == listing_data['listing_id']
            ).first()
            
            if existing_listing:
                # Update existing listing
                for key, value in listing_data.items():
                    if hasattr(existing_listing, key):
                        setattr(existing_listing, key, value)
                existing_listing.scraped_at = datetime.utcnow()
                logger.info(f"Updated existing listing: {listing_data['listing_id']}")
                session.commit()
                return False  # Not a new listing
            else:
                # Add new listing
                new_listing = CarListing(**listing_data)
                session.add(new_listing)
                session.commit()
                logger.info(f"Added new listing: {listing_data['listing_id']}")
                return True  # New listing added
                
        except Exception as e:
            session.rollback()
            logger.error(f"Error adding/updating listing {listing_data.get('listing_id', 'unknown')}: {str(e)}")
            return False
        finally:
            session.close()

    def upsert_verified_listing(self, listing_data: Dict[str, Any], observed_at: Optional[datetime] = None) -> bool:
        """Store a verified live OLX listing and append an immutable price snapshot.

        Returns True only when the listing has not been seen before. Every successful
        collection records a snapshot, including when the asking price did not change.
        """
        observed_at = observed_at or datetime.utcnow()
        session = self.get_session()
        try:
            listing_id = str(listing_data['listing_id'])
            existing_listing = session.query(CarListing).filter(CarListing.listing_id == listing_id).first()
            fields = {
                key: value for key, value in listing_data.items()
                if key in {
                    'make', 'model', 'year', 'price', 'mileage', 'views', 'posted_date',
                    'location', 'seller_type', 'fuel_type', 'transmission', 'listing_url', 'description'
                } and value is not None
            }

            if existing_listing:
                for key, value in fields.items():
                    setattr(existing_listing, key, value)
                existing_listing.scraped_at = observed_at
                existing_listing.last_verified_at = observed_at
                existing_listing.source = 'olx.ba'
                existing_listing.collection_method = listing_data.get('collection_method', 'olx_api')
                existing_listing.is_active = True
                is_new = False
            else:
                existing_listing = CarListing(
                    listing_id=listing_id,
                    source='olx.ba',
                    collection_method=listing_data.get('collection_method', 'olx_api'),
                    first_seen_at=observed_at,
                    last_verified_at=observed_at,
                    scraped_at=observed_at,
                    is_active=True,
                    **fields,
                )
                session.add(existing_listing)
                is_new = True

            session.add(ListingSnapshot(
                listing_id=listing_id,
                observed_at=observed_at,
                asking_price=listing_data['price'],
                views=listing_data.get('views'),
                source_url=listing_data['listing_url'],
                source_query=listing_data.get('source_query'),
                source_page=listing_data.get('source_page'),
                title=listing_data['title'],
                is_active=True,
            ))
            session.commit()
            return is_new
        except Exception:
            session.rollback()
            logger.exception('Unable to store verified OLX listing')
            raise
        finally:
            session.close()

    def get_latest_comparable_run_listing_ids(self, source_query: str, pages_requested: int) -> set[str]:
        """Return IDs from the latest successful run with the same source and scope."""
        session = self.get_session()
        try:
            run = session.query(ScrapingLog).filter(
                ScrapingLog.status == 'completed',
                ScrapingLog.source_query == source_query,
                ScrapingLog.pages_requested == pages_requested,
                ScrapingLog.end_time.isnot(None),
            ).order_by(desc(ScrapingLog.start_time)).first()
            if not run:
                return set()

            rows = session.query(ListingSnapshot.listing_id).filter(
                ListingSnapshot.source_query == source_query,
                ListingSnapshot.observed_at >= run.start_time,
                ListingSnapshot.observed_at <= run.end_time,
            ).distinct().all()
            return {row.listing_id for row in rows}
        finally:
            session.close()

    def purge_generated_data(self) -> int:
        """Remove only records created by the repository's demo-data generators."""
        session = self.get_session()
        try:
            generated_prefixes = ('sample_%', 'today_%', 'hot_%', 'real_%', 'recent_%')
            deleted = session.query(CarListing).filter(
                or_(*(CarListing.listing_id.like(prefix) for prefix in generated_prefixes))
            ).delete(synchronize_session=False)
            session.commit()
            logger.info('Removed %s generated listings', deleted)
            return deleted
        except Exception:
            session.rollback()
            logger.exception('Unable to remove generated listings')
            raise
        finally:
            session.close()
    
    def get_active_listings(self, limit: Optional[int] = None) -> List[CarListing]:
        """Get all active listings"""
        session = self.get_session()
        try:
            query = session.query(CarListing).filter(CarListing.is_active == True)
            if limit:
                query = query.limit(limit)
            return query.all()
        finally:
            session.close()
    
    def get_todays_listings(self) -> List[CarListing]:
        """Get listings posted today"""
        session = self.get_session()
        try:
            today = date.today()
            return session.query(CarListing).filter(
                and_(
                    CarListing.posted_date == today,
                    CarListing.is_active == True
                )
            ).order_by(desc(CarListing.scraped_at)).all()
        finally:
            session.close()
    
    def get_hot_cars(self, days: int = 7, limit: int = 20) -> List[CarListing]:
        """Get cars with high views from recent days"""
        session = self.get_session()
        try:
            cutoff_date = date.today() - timedelta(days=days)
            return session.query(CarListing).filter(
                and_(
                    CarListing.posted_date >= cutoff_date,
                    CarListing.is_active == True,
                    CarListing.views > 0
                )
            ).order_by(desc(CarListing.views)).limit(limit).all()
        finally:
            session.close()
    
    def get_market_stats(self) -> Dict[str, Any]:
        """Get market statistics"""
        session = self.get_session()
        try:
            stats = {}
            
            verified_inventory = (
                CarListing.is_active == True,
                CarListing.collection_method == 'olx_api',
            )

            # Total active listings from the stable API collector.
            stats['total_active'] = session.query(CarListing).filter(
                *verified_inventory
            ).count()
            
            # Listings first observed today (not their seller-provided posting date).
            today = date.today()
            stats['new_today'] = session.query(CarListing).filter(
                and_(
                    func.date(CarListing.first_seen_at) == today,
                    *verified_inventory
                )
            ).count()
            
            # Average price
            avg_price = session.query(func.avg(CarListing.price)).filter(
                and_(
                    *verified_inventory,
                    CarListing.price.isnot(None),
                    CarListing.price > 0
                )
            ).scalar()
            stats['avg_price'] = round(avg_price) if avg_price else 0
            
            # Most viewed listing
            most_viewed = session.query(CarListing).filter(
                and_(
                    *verified_inventory,
                    CarListing.views > 0
                )
            ).order_by(desc(CarListing.views)).first()
            
            if most_viewed:
                stats['most_viewed'] = {
                    'make': most_viewed.make,
                    'model': most_viewed.model,
                    'year': most_viewed.year,
                    'price': most_viewed.price,
                    'views': most_viewed.views
                }
            else:
                stats['most_viewed'] = None
                
            return stats
        finally:
            session.close()
    
    def get_top_makes(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Get top car makes by listing count"""
        session = self.get_session()
        try:
            results = session.query(
                CarListing.make,
                func.count(CarListing.listing_id).label('count')
            ).filter(
                CarListing.is_active == True,
                CarListing.collection_method == 'olx_api',
            ).group_by(CarListing.make).order_by(desc('count')).limit(limit).all()
            
            return [{'make': result.make, 'count': result.count} for result in results]
        finally:
            session.close()
    
    def get_top_models(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Get top car models by listing count"""
        session = self.get_session()
        try:
            results = session.query(
                CarListing.make,
                CarListing.model,
                func.count(CarListing.listing_id).label('count')
            ).filter(
                CarListing.is_active == True,
                CarListing.collection_method == 'olx_api',
            ).group_by(CarListing.make, CarListing.model).order_by(desc('count')).limit(limit).all()
            
            return [{'make': result.make, 'model': result.model, 'count': result.count} for result in results]
        finally:
            session.close()
    
    def search_cars(self, make: str = None, model: str = None, 
                   year_min: int = None, year_max: int = None,
                   mileage_min: int = None, mileage_max: int = None,
                   price_min: int = None, price_max: int = None) -> List[CarListing]:
        """Search cars with filters"""
        session = self.get_session()
        try:
            query = session.query(CarListing).filter(CarListing.is_active == True)
            
            if make:
                query = query.filter(CarListing.make.ilike(f'%{make}%'))
            if model:
                query = query.filter(CarListing.model.ilike(f'%{model}%'))
            if year_min:
                query = query.filter(CarListing.year >= year_min)
            if year_max:
                query = query.filter(CarListing.year <= year_max)
            if mileage_min:
                query = query.filter(CarListing.mileage >= mileage_min)
            if mileage_max:
                query = query.filter(CarListing.mileage <= mileage_max)
            if price_min:
                query = query.filter(CarListing.price >= price_min)
            if price_max:
                query = query.filter(CarListing.price <= price_max)
                
            return query.order_by(desc(CarListing.scraped_at)).all()
        finally:
            session.close()
    
    def get_makes_list(self) -> List[str]:
        """Get list of all unique makes"""
        session = self.get_session()
        try:
            results = session.query(CarListing.make).filter(
                CarListing.is_active == True
            ).distinct().order_by(CarListing.make).all()
            return [result.make for result in results if result.make]
        finally:
            session.close()
    
    def get_models_for_make(self, make: str) -> List[str]:
        """Get list of models for a specific make"""
        session = self.get_session()
        try:
            results = session.query(CarListing.model).filter(
                and_(
                    CarListing.is_active == True,
                    CarListing.make == make
                )
            ).distinct().order_by(CarListing.model).all()
            return [result.model for result in results if result.model]
        finally:
            session.close()
    
    def mark_listings_inactive(self, listing_ids: List[str]) -> int:
        """Mark listings as inactive (sold/removed)"""
        session = self.get_session()
        try:
            count = session.query(CarListing).filter(
                CarListing.listing_id.in_(listing_ids)
            ).update({CarListing.is_active: False}, synchronize_session=False)
            session.commit()
            logger.info(f"Marked {count} listings as inactive")
            return count
        except Exception as e:
            session.rollback()
            logger.error(f"Error marking listings inactive: {str(e)}")
            return 0
        finally:
            session.close()
    
    def add_scraping_log(self, session_id: str, **kwargs) -> int:
        """Add a scraping log entry"""
        session = self.get_session()
        try:
            log_entry = ScrapingLog(session_id=session_id, **kwargs)
            session.add(log_entry)
            session.commit()
            return log_entry.id
        except Exception as e:
            session.rollback()
            logger.error(f"Error adding scraping log: {str(e)}")
            return None
        finally:
            session.close()
    
    def update_scraping_log(self, log_id: int, **kwargs):
        """Update a scraping log entry"""
        session = self.get_session()
        try:
            log_entry = session.query(ScrapingLog).filter(ScrapingLog.id == log_id).first()
            if log_entry:
                for key, value in kwargs.items():
                    if hasattr(log_entry, key):
                        setattr(log_entry, key, value)
                session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Error updating scraping log: {str(e)}")
        finally:
            session.close()
    
    def cleanup_old_data(self, days: int = 30):
        """Clean up old inactive listings"""
        session = self.get_session()
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            count = session.query(CarListing).filter(
                and_(
                    CarListing.is_active == False,
                    CarListing.scraped_at < cutoff_date
                )
            ).delete(synchronize_session=False)
            session.commit()
            logger.info(f"Cleaned up {count} old inactive listings")
            return count
        except Exception as e:
            session.rollback()
            logger.error(f"Error cleaning up old data: {str(e)}")
            return 0
        finally:
            session.close()

# Create a global instance
db_manager = DatabaseManager()
