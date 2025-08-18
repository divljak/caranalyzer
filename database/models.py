"""
Database models for OLX Car Scraper
"""
from sqlalchemy import create_engine, Column, String, Integer, Date, DateTime, Boolean, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import sys
import os

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import DATABASE

Base = declarative_base()

class CarListing(Base):
    """Model for car listings scraped from OLX.ba"""
    __tablename__ = 'car_listings'
    
    listing_id = Column(String(50), primary_key=True)
    make = Column(String(50), nullable=False)
    model = Column(String(100), nullable=False)
    year = Column(Integer)
    price = Column(Integer)  # Price in Bosnian Marks (KM)
    mileage = Column(Integer)  # In kilometers
    views = Column(Integer, default=0)
    posted_date = Column(Date)
    location = Column(String(100))  # City/region in Bosnia
    seller_type = Column(String(20))  # 'individual' or 'dealer'
    fuel_type = Column(String(20))  # 'petrol', 'diesel', 'hybrid', 'electric'
    transmission = Column(String(20))  # 'manual', 'automatic'
    listing_url = Column(String(500))
    description = Column(Text)
    scraped_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    
    def __repr__(self):
        return f"<CarListing(id='{self.listing_id}', make='{self.make}', model='{self.model}', year={self.year}, price={self.price})>"

class ScrapingLog(Base):
    """Model for tracking scraping sessions"""
    __tablename__ = 'scraping_logs'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(50), nullable=False)
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime)
    total_listings_found = Column(Integer, default=0)
    new_listings_added = Column(Integer, default=0)
    updated_listings = Column(Integer, default=0)
    errors_count = Column(Integer, default=0)
    status = Column(String(20), default='running')  # 'running', 'completed', 'failed'
    error_message = Column(Text)
    
    def __repr__(self):
        return f"<ScrapingLog(session_id='{self.session_id}', status='{self.status}', total_listings={self.total_listings_found})>"

def get_database_url():
    """Get database URL from settings"""
    db_config = DATABASE
    return f"postgresql://{db_config['user']}:{db_config['password']}@{db_config['host']}:{db_config['port']}/{db_config['database']}"

def create_engine_and_session():
    """Create database engine and session"""
    engine = create_engine(get_database_url())
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine, SessionLocal

def create_tables():
    """Create all tables in the database"""
    engine, _ = create_engine_and_session()
    Base.metadata.create_all(bind=engine)
    print("Database tables created successfully!")

if __name__ == "__main__":
    # Create tables when run directly
    create_tables()