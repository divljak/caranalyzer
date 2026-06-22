"""
Database models for OLX Car Scraper
"""
from sqlalchemy import create_engine, Column, String, Integer, Date, DateTime, Boolean, Text, ForeignKey, Index, inspect, text
from sqlalchemy.engine import URL
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
    source = Column(String(30), nullable=False, default='olx.ba')
    collection_method = Column(String(30), nullable=False, default='olx_api')
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_verified_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    
    def __repr__(self):
        return f"<CarListing(id='{self.listing_id}', make='{self.make}', model='{self.model}', year={self.year}, price={self.price})>"


class ListingSnapshot(Base):
    """An immutable observation of a live OLX listing at collection time."""
    __tablename__ = 'listing_snapshots'

    id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(String(50), ForeignKey('car_listings.listing_id', ondelete='CASCADE'), nullable=False)
    observed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    asking_price = Column(Integer, nullable=False)
    views = Column(Integer)
    source_url = Column(String(500), nullable=False)
    source_query = Column(String(500))
    source_page = Column(Integer)
    title = Column(String(500), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index('ix_listing_snapshots_listing_observed', 'listing_id', 'observed_at'),
    )

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
    source_query = Column(String(500))
    pages_requested = Column(Integer)
    
    def __repr__(self):
        return f"<ScrapingLog(session_id='{self.session_id}', status='{self.status}', total_listings={self.total_listings_found})>"

def get_database_url():
    """Get a safe database connection URL from production or local settings."""
    database_url = os.getenv('DATABASE_URL')
    if database_url:
        return database_url

    db_config = DATABASE
    return URL.create(
        'postgresql+psycopg2',
        username=db_config['user'],
        password=db_config['password'],
        host=db_config['host'],
        port=int(db_config['port']),
        database=db_config['database'],
    )

def create_engine_and_session():
    """Create database engine and session"""
    engine = create_engine(get_database_url())
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine, SessionLocal

def create_tables():
    """Create tables and apply the small backwards-compatible schema migration."""
    engine, _ = create_engine_and_session()
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        migrations = {
            'car_listings': {
                'source': "ALTER TABLE car_listings ADD COLUMN source VARCHAR(30) NOT NULL DEFAULT 'olx.ba'",
                'collection_method': "ALTER TABLE car_listings ADD COLUMN collection_method VARCHAR(30) NOT NULL DEFAULT 'legacy_rendered'",
                'first_seen_at': 'ALTER TABLE car_listings ADD COLUMN first_seen_at TIMESTAMP',
                'last_verified_at': 'ALTER TABLE car_listings ADD COLUMN last_verified_at TIMESTAMP',
            },
            'listing_snapshots': {
                'source_query': 'ALTER TABLE listing_snapshots ADD COLUMN source_query VARCHAR(500)',
                'source_page': 'ALTER TABLE listing_snapshots ADD COLUMN source_page INTEGER',
            },
            'scraping_logs': {
                'source_query': 'ALTER TABLE scraping_logs ADD COLUMN source_query VARCHAR(500)',
                'pages_requested': 'ALTER TABLE scraping_logs ADD COLUMN pages_requested INTEGER',
            },
        }
        for table_name, table_migrations in migrations.items():
            existing_columns = {column['name'] for column in inspect(connection).get_columns(table_name)}
            for column, statement in table_migrations.items():
                if column not in existing_columns:
                    connection.execute(text(statement))
    print("Database tables created successfully!")

if __name__ == "__main__":
    # Create tables when run directly
    create_tables()
