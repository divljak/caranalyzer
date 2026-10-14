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
    price = Column(Integer)  # Price in Bosnian Marks (KM); 0 means missing/on-request when price_status != 'priced'
    price_status = Column(String(30), nullable=False, default='priced')  # 'priced' or 'missing'
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
    first_price = Column(Integer)
    previous_price = Column(Integer)
    price_change_count = Column(Integer, nullable=False, default=0)
    last_price_change_at = Column(DateTime)
    last_source_query = Column(String(500))
    missing_from_window_since_at = Column(DateTime)
    consecutive_missing_runs = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, default=True)
    
    def __repr__(self):
        return f"<CarListing(id='{self.listing_id}', make='{self.make}', model='{self.model}', year={self.year}, price={self.price})>"


class ListingSnapshot(Base):
    """An immutable observation of a live OLX listing at collection time."""
    __tablename__ = 'listing_snapshots'

    id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(String(50), ForeignKey('car_listings.listing_id', ondelete='CASCADE'), nullable=False)
    observed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    asking_price = Column(Integer, nullable=False)  # 0 means missing/on-request when price_status != 'priced'
    price_status = Column(String(30), nullable=False, default='priced')
    views = Column(Integer)
    source_url = Column(String(500), nullable=False)
    source_query = Column(String(500))
    source_page = Column(Integer)
    source_per_page = Column(Integer)
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
    source_per_page = Column(Integer)
    missing_from_window_count = Column(Integer, nullable=False, default=0)
    
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
                'price_status': "ALTER TABLE car_listings ADD COLUMN price_status VARCHAR(30) NOT NULL DEFAULT 'priced'",
                'first_price': 'ALTER TABLE car_listings ADD COLUMN first_price INTEGER',
                'previous_price': 'ALTER TABLE car_listings ADD COLUMN previous_price INTEGER',
                'price_change_count': 'ALTER TABLE car_listings ADD COLUMN price_change_count INTEGER NOT NULL DEFAULT 0',
                'last_price_change_at': 'ALTER TABLE car_listings ADD COLUMN last_price_change_at TIMESTAMP',
                'last_source_query': 'ALTER TABLE car_listings ADD COLUMN last_source_query VARCHAR(500)',
                'missing_from_window_since_at': 'ALTER TABLE car_listings ADD COLUMN missing_from_window_since_at TIMESTAMP',
                'consecutive_missing_runs': 'ALTER TABLE car_listings ADD COLUMN consecutive_missing_runs INTEGER NOT NULL DEFAULT 0',
            },
            'listing_snapshots': {
                'source_query': 'ALTER TABLE listing_snapshots ADD COLUMN source_query VARCHAR(500)',
                'source_page': 'ALTER TABLE listing_snapshots ADD COLUMN source_page INTEGER',
                'source_per_page': 'ALTER TABLE listing_snapshots ADD COLUMN source_per_page INTEGER',
                'price_status': "ALTER TABLE listing_snapshots ADD COLUMN price_status VARCHAR(30) NOT NULL DEFAULT 'priced'",
            },
            'scraping_logs': {
                'source_query': 'ALTER TABLE scraping_logs ADD COLUMN source_query VARCHAR(500)',
                'pages_requested': 'ALTER TABLE scraping_logs ADD COLUMN pages_requested INTEGER',
                'source_per_page': 'ALTER TABLE scraping_logs ADD COLUMN source_per_page INTEGER',
                'missing_from_window_count': 'ALTER TABLE scraping_logs ADD COLUMN missing_from_window_count INTEGER NOT NULL DEFAULT 0',
            },
        }
        for table_name, table_migrations in migrations.items():
            existing_columns = {column['name'] for column in inspect(connection).get_columns(table_name)}
            for column, statement in table_migrations.items():
                if column not in existing_columns:
                    connection.execute(text(statement))
        connection.execute(text("""
            UPDATE car_listings
            SET first_price = price
            WHERE collection_method = 'olx_api'
              AND first_price IS NULL
              AND price IS NOT NULL
        """))
        connection.execute(text("""
            UPDATE car_listings cl
            SET last_source_query = latest.source_query
            FROM (
                SELECT DISTINCT ON (listing_id) listing_id, source_query
                FROM listing_snapshots
                WHERE source_query IS NOT NULL
                ORDER BY listing_id, observed_at DESC
            ) latest
            WHERE cl.listing_id = latest.listing_id
              AND cl.collection_method = 'olx_api'
              AND cl.last_source_query IS NULL
        """))
    print("Database tables created successfully!")

if __name__ == "__main__":
    # Create tables when run directly
    create_tables()
