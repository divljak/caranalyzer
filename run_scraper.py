#!/usr/bin/env python3
"""
Main entry point for OLX Car Scraper
"""
import sys
import os
import logging
import argparse
from pathlib import Path

# Add current directory to Python path
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

# Scrapy imports removed - using direct approach
from database.models import create_tables
from database.db_manager import db_manager
from config.settings import LOGGING

def setup_logging():
    """Setup logging configuration"""
    logging.basicConfig(
        level=getattr(logging, LOGGING['level']),
        format=LOGGING['format'],
        handlers=[
            logging.FileHandler(LOGGING['file']),
            logging.StreamHandler()
        ]
    )
    
    # Reduce Scrapy logging noise
    logging.getLogger('scrapy').setLevel(logging.WARNING)
    logging.getLogger('selenium').setLevel(logging.WARNING)

def setup_database():
    """Setup database tables"""
    try:
        create_tables()
        logging.info("Database setup completed")
        return True
    except Exception as e:
        logging.error(f"Database setup failed: {str(e)}")
        return False

def cleanup_old_data(days: int = 30):
    """Clean up old inactive listings"""
    try:
        count = db_manager.cleanup_old_data(days)
        logging.info(f"Cleaned up {count} old inactive listings")
    except Exception as e:
        logging.error(f"Error during cleanup: {str(e)}")

def run_spider(pages: int = 1, allow_zero_overlap: bool = False):
    """Collect a bounded set of live OLX listings and retain price snapshots."""
    log_id = None
    try:
        logging.info("Starting verified OLX API asking-price collection...")
        from scrapers.olx_api_collector import OLXAPICollector, SOURCE_QUERY
        import uuid
        from datetime import datetime, timezone
        
        session_id = str(uuid.uuid4())
        start_time = datetime.now(timezone.utc).replace(tzinfo=None)
        previous_listing_ids = db_manager.get_latest_comparable_run_listing_ids(SOURCE_QUERY, pages)
        
        # Add scraping log
        log_id = db_manager.add_scraping_log(
            session_id=session_id,
            start_time=start_time,
            status='running',
            source_query=SOURCE_QUERY,
            pages_requested=pages,
        )
        
        total_listings = 0
        new_listings = 0
        errors = 0
        
        collector = OLXAPICollector()
        listings = collector.collect(pages=pages)
        for page_stat in collector.page_stats:
            logging.info('OLX API page stats: %s', page_stat)

        listing_ids = {listing['listing_id'] for listing in listings}
        overlap_count = len(listing_ids.intersection(previous_listing_ids))
        if previous_listing_ids and overlap_count == 0 and not allow_zero_overlap:
            message = (
                'Collection rejected: zero overlap with the previous comparable OLX API run. '
                'Use --allow-zero-overlap only after investigating the source.'
            )
            db_manager.update_scraping_log(
                log_id,
                end_time=datetime.now(timezone.utc).replace(tzinfo=None),
                total_listings_found=len(listings),
                errors_count=1,
                status='failed',
                error_message=message,
            )
            logging.error(message)
            return False
        logging.info(
            'OLX API overlap with previous comparable run: %s/%s listings',
            overlap_count,
            len(listing_ids),
        )

        for data in listings:
            try:
                if db_manager.upsert_verified_listing(data, observed_at=start_time):
                    new_listings += 1
                total_listings += 1
            except Exception as exc:
                logging.error('Could not persist listing %s: %s', data.get('listing_id'), exc)
                errors += 1
        
        # Update log
        end_time = datetime.now(timezone.utc).replace(tzinfo=None)
        if log_id:
            db_manager.update_scraping_log(
                log_id=log_id,
                end_time=end_time,
                total_listings_found=total_listings,
                new_listings_added=new_listings,
                errors_count=errors,
                status='completed'
            )
        
        logging.info(
            'Verified collection completed: %s listings observed, %s new, %s errors',
            total_listings,
            new_listings,
            errors,
        )
        return True
        
    except Exception as e:
        if log_id:
            db_manager.update_scraping_log(
                log_id,
                end_time=datetime.now(timezone.utc).replace(tzinfo=None),
                errors_count=1,
                status='failed',
                error_message=str(e),
            )
        logging.error(f"Error running spider: {str(e)}")
        return False

def get_stats():
    """Get and display current database statistics"""
    try:
        stats = db_manager.get_market_stats()
        
        print("\n" + "="*50)
        print("DATABASE STATISTICS")
        print("="*50)
        print(f"Total active listings: {stats['total_active']}")
        print(f"New listings today: {stats['new_today']}")
        print(f"Average price: {stats['avg_price']} KM")
        
        if stats['most_viewed']:
            mv = stats['most_viewed']
            print(f"Most viewed: {mv['make']} {mv['model']} {mv['year']} - {mv['price']} KM ({mv['views']} views)")
        
        print("\nTop 5 Makes:")
        top_makes = db_manager.get_top_makes(5)
        for make_data in top_makes:
            print(f"  {make_data['make']}: {make_data['count']} listings")
        
        print("\nTop 5 Models:")
        top_models = db_manager.get_top_models(5)
        for model_data in top_models:
            print(f"  {model_data['make']} {model_data['model']}: {model_data['count']} listings")
        
        print("="*50)
        
    except Exception as e:
        logging.error(f"Error getting statistics: {str(e)}")

def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='OLX Car Scraper')
    parser.add_argument('--setup-db', action='store_true', 
                       help='Setup database tables only')
    parser.add_argument('--cleanup', type=int, metavar='DAYS', default=30,
                       help='Clean up inactive listings older than N days')
    parser.add_argument('--stats', action='store_true',
                       help='Show database statistics')
    parser.add_argument('--no-scrape', action='store_true',
                       help='Skip scraping (useful with --stats or --cleanup)')
    parser.add_argument('--pages', type=int, default=1, choices=range(1, 6),
                       help='Number of OLX result pages to collect (1-5; default: 1)')
    parser.add_argument('--purge-generated', action='store_true',
                       help='Remove only the repository-generated demo listings before collecting live data')
    parser.add_argument('--allow-zero-overlap', action='store_true',
                       help='Permit a zero-overlap API run after an investigated source change')
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging()
    logger = logging.getLogger(__name__)
    
    logger.info("Starting OLX Car Scraper application")
    
    # Ensure the listing and snapshot tables are available before every collection.
    if not setup_database():
        logger.error("Database setup failed, exiting")
        sys.exit(1)
    
    # Clean up old data
    cleanup_old_data(args.cleanup)
    
    # Show statistics if requested
    if args.stats:
        get_stats()

    if args.purge_generated:
        removed = db_manager.purge_generated_data()
        logger.info('Removed %s generated listings', removed)
    
    # Run scraper unless explicitly disabled
    if not args.no_scrape:
        success = run_spider(args.pages, args.allow_zero_overlap)
        if not success:
            logger.error("Scraping failed")
            sys.exit(1)
    
    # Show final statistics
    if not args.no_scrape:
        get_stats()
    
    logger.info("Application completed successfully")

if __name__ == '__main__':
    main()
