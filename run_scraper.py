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

def run_spider():
    """Run the OLX spider using direct approach"""
    try:
        logging.info("Starting OLX car scraper...")
        
        # Import and use the selenium handler directly for now
        from scrapers.selenium_handler import SeleniumHandler
        from database.db_manager import db_manager
        from config.settings import SCRAPING
        import uuid
        from datetime import datetime
        
        session_id = str(uuid.uuid4())
        start_time = datetime.utcnow()
        
        # Add scraping log
        log_id = db_manager.add_scraping_log(
            session_id=session_id,
            start_time=start_time,
            status='running'
        )
        
        total_listings = 0
        new_listings = 0
        errors = 0
        
        # Use Selenium handler directly
        with SeleniumHandler() as handler:
            base_url = SCRAPING['base_url']
            page = 1
            max_pages = min(5, SCRAPING['max_pages'])  # Limit to 5 pages for testing
            
            while page <= max_pages:
                url = f"{base_url}&page={page}"
                logging.info(f"Processing page {page}: {url}")
                
                if not handler.get_page(url):
                    logging.error(f"Failed to load page {page}")
                    break
                
                # Get listing cards
                cards = handler.get_listing_cards()
                if not cards:
                    logging.info(f"No more listings found on page {page}")
                    break
                
                logging.info(f"Found {len(cards)} listings on page {page}")
                
                # Process each card
                for card in cards:
                    try:
                        data = handler.extract_card_data(card)
                        if data and data.get('listing_id'):
                            # Clean and validate data
                            from utils.data_validator import validator
                            cleaned_data = validator.validate_listing(data)
                            
                            if cleaned_data:
                                is_new = db_manager.add_or_update_listing(cleaned_data)
                                if is_new:
                                    new_listings += 1
                                total_listings += 1
                    except Exception as e:
                        logging.error(f"Error processing listing: {str(e)}")
                        errors += 1
                
                page += 1
                
                # Add delay between pages
                import time, random
                time.sleep(random.uniform(*SCRAPING['delay_range']))
        
        # Update log
        end_time = datetime.utcnow()
        if log_id:
            db_manager.update_scraping_log(
                log_id=log_id,
                end_time=end_time,
                total_listings_found=total_listings,
                new_listings_added=new_listings,
                errors_count=errors,
                status='completed'
            )
        
        logging.info(f"Scraping completed: {total_listings} total, {new_listings} new, {errors} errors")
        return True
        
    except Exception as e:
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
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging()
    logger = logging.getLogger(__name__)
    
    logger.info("Starting OLX Car Scraper application")
    
    # Setup database if requested or if it's the first run
    if args.setup_db or not os.path.exists('olx_scraper.log'):
        if not setup_database():
            logger.error("Database setup failed, exiting")
            sys.exit(1)
    
    # Clean up old data
    cleanup_old_data(args.cleanup)
    
    # Show statistics if requested
    if args.stats:
        get_stats()
    
    # Run scraper unless explicitly disabled
    if not args.no_scrape:
        success = run_spider()
        if not success:
            logger.error("Scraping failed")
            sys.exit(1)
    
    # Show final statistics
    if not args.no_scrape:
        get_stats()
    
    logger.info("Application completed successfully")

if __name__ == '__main__':
    main()