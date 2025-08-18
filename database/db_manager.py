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

from database.models import CarListing, ScrapingLog, create_engine_and_session
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
            
            # Total active listings
            stats['total_active'] = session.query(CarListing).filter(
                CarListing.is_active == True
            ).count()
            
            # New listings today
            today = date.today()
            stats['new_today'] = session.query(CarListing).filter(
                and_(
                    CarListing.posted_date == today,
                    CarListing.is_active == True
                )
            ).count()
            
            # Average price
            avg_price = session.query(func.avg(CarListing.price)).filter(
                and_(
                    CarListing.is_active == True,
                    CarListing.price.isnot(None),
                    CarListing.price > 0
                )
            ).scalar()
            stats['avg_price'] = round(avg_price) if avg_price else 0
            
            # Most viewed listing
            most_viewed = session.query(CarListing).filter(
                and_(
                    CarListing.is_active == True,
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
                CarListing.is_active == True
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
                CarListing.is_active == True
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