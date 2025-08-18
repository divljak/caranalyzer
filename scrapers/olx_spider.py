"""
Main Scrapy spider for OLX.ba car listings
"""
import scrapy
import time
import random
import logging
from datetime import datetime
import uuid
import sys
import os

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers.selenium_handler import SeleniumHandler
from database.db_manager import db_manager
from config.settings import SCRAPING, USER_AGENTS, VALIDATION

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OLXCarSpider(scrapy.Spider):
    """Scrapy spider for OLX.ba car listings"""
    
    name = 'olx_cars'
    allowed_domains = ['olx.ba']
    
    def __init__(self):
        super().__init__()
        self.session_id = str(uuid.uuid4())
        self.start_time = datetime.utcnow()
        self.total_listings_found = 0
        self.new_listings_added = 0
        self.updated_listings = 0
        self.errors_count = 0
        self.selenium_handler = None
        
        # Add scraping log
        self.log_id = db_manager.add_scraping_log(
            session_id=self.session_id,
            start_time=self.start_time,
            status='running'
        )
        
        logger.info(f"Started scraping session: {self.session_id}")
    
    def start_requests(self):
        """Generate initial requests"""
        base_url = SCRAPING['base_url']
        
        # Start with first page
        yield scrapy.Request(
            url=base_url,
            callback=self.parse_with_selenium,
            meta={'page': 1}
        )
    
    def parse_with_selenium(self, response):
        """Parse using Selenium for JavaScript rendering"""
        page = response.meta.get('page', 1)
        logger.info(f"Processing page {page}: {response.url}")
        
        try:
            # Setup Selenium handler if not already done
            if not self.selenium_handler:
                self.selenium_handler = SeleniumHandler()
                if not self.selenium_handler.setup_driver():
                    logger.error("Failed to setup Selenium driver")
                    return
            
            # Load page with Selenium
            if not self.selenium_handler.get_page(response.url):
                logger.error(f"Failed to load page with Selenium: {response.url}")
                return
            
            # Extract listing cards
            listing_cards = self.selenium_handler.get_listing_cards()
            
            if not listing_cards:
                logger.warning(f"No listing cards found on page {page}")
                return
            
            logger.info(f"Found {len(listing_cards)} listings on page {page}")
            
            # Process each listing card
            for card in listing_cards:
                try:
                    listing_data = self.selenium_handler.extract_card_data(card)
                    
                    if listing_data and listing_data.get('listing_id'):
                        # Validate and clean data
                        cleaned_data = self.clean_listing_data(listing_data)
                        
                        if cleaned_data:
                            # Save to database
                            is_new = db_manager.add_or_update_listing(cleaned_data)
                            
                            if is_new:
                                self.new_listings_added += 1
                            else:
                                self.updated_listings += 1
                            
                            self.total_listings_found += 1
                            
                            # Yield for further processing if needed
                            yield cleaned_data
                        
                except Exception as e:
                    logger.error(f"Error processing listing card: {str(e)}")
                    self.errors_count += 1
            
            # Check for next page
            next_page_url = self.selenium_handler.check_next_page()
            
            if next_page_url and page < SCRAPING['max_pages']:
                logger.info(f"Following to next page: {next_page_url}")
                
                # Add random delay between pages
                delay = random.uniform(*SCRAPING['delay_range'])
                time.sleep(delay)
                
                yield scrapy.Request(
                    url=next_page_url,
                    callback=self.parse_with_selenium,
                    meta={'page': page + 1}
                )
            else:
                logger.info(f"Reached end of pagination or max pages limit on page {page}")
        
        except Exception as e:
            logger.error(f"Error in parse_with_selenium: {str(e)}")
            self.errors_count += 1
    
    def clean_listing_data(self, raw_data: dict) -> dict:
        """Clean and validate listing data"""
        try:
            cleaned = {}
            
            # Required fields
            required_fields = ['listing_id', 'listing_url']
            for field in required_fields:
                if field not in raw_data or not raw_data[field]:
                    logger.warning(f"Missing required field '{field}' in listing data")
                    return None
                cleaned[field] = raw_data[field]
            
            # Clean make and model
            if 'make' in raw_data and raw_data['make']:
                cleaned['make'] = str(raw_data['make']).strip().title()[:50]
            
            if 'model' in raw_data and raw_data['model']:
                cleaned['model'] = str(raw_data['model']).strip().title()[:100]
            
            # Validate and clean year
            if 'year' in raw_data and raw_data['year']:
                try:
                    year = int(raw_data['year'])
                    if VALIDATION['min_year'] <= year <= VALIDATION['max_year']:
                        cleaned['year'] = year
                except (ValueError, TypeError):
                    logger.warning(f"Invalid year value: {raw_data['year']}")
            
            # Validate and clean price
            if 'price' in raw_data and raw_data['price']:
                try:
                    price = int(raw_data['price'])
                    if VALIDATION['min_price'] <= price <= VALIDATION['max_price']:
                        cleaned['price'] = price
                    else:
                        logger.warning(f"Price {price} outside valid range")
                except (ValueError, TypeError):
                    logger.warning(f"Invalid price value: {raw_data['price']}")
            
            # Validate and clean mileage
            if 'mileage' in raw_data and raw_data['mileage']:
                try:
                    mileage = int(raw_data['mileage'])
                    if 0 <= mileage <= VALIDATION['max_mileage']:
                        cleaned['mileage'] = mileage
                    else:
                        logger.warning(f"Mileage {mileage} outside valid range")
                except (ValueError, TypeError):
                    logger.warning(f"Invalid mileage value: {raw_data['mileage']}")
            
            # Clean location
            if 'location' in raw_data and raw_data['location']:
                cleaned['location'] = str(raw_data['location']).strip()[:100]
            
            # Clean fuel type
            if 'fuel_type' in raw_data and raw_data['fuel_type']:
                fuel_type = str(raw_data['fuel_type']).lower().strip()
                valid_fuels = ['petrol', 'diesel', 'hybrid', 'electric', 'gas']
                if fuel_type in valid_fuels:
                    cleaned['fuel_type'] = fuel_type
            
            # Clean transmission
            if 'transmission' in raw_data and raw_data['transmission']:
                transmission = str(raw_data['transmission']).lower().strip()
                valid_transmissions = ['manual', 'automatic']
                if transmission in valid_transmissions:
                    cleaned['transmission'] = transmission
            
            # Set posted date
            if 'posted_date' in raw_data and raw_data['posted_date']:
                cleaned['posted_date'] = raw_data['posted_date']
            else:
                cleaned['posted_date'] = datetime.now().date()
            
            # Set default values
            cleaned['views'] = raw_data.get('views', 0)
            cleaned['seller_type'] = raw_data.get('seller_type', 'individual')
            cleaned['scraped_at'] = datetime.utcnow()
            cleaned['is_active'] = True
            
            return cleaned
            
        except Exception as e:
            logger.error(f"Error cleaning listing data: {str(e)}")
            return None
    
    def closed(self, reason):
        """Called when spider is closed"""
        end_time = datetime.utcnow()
        
        # Update scraping log
        if self.log_id:
            db_manager.update_scraping_log(
                log_id=self.log_id,
                end_time=end_time,
                total_listings_found=self.total_listings_found,
                new_listings_added=self.new_listings_added,
                updated_listings=self.updated_listings,
                errors_count=self.errors_count,
                status='completed' if reason == 'finished' else 'failed'
            )
        
        # Close Selenium handler
        if self.selenium_handler:
            self.selenium_handler.close()
        
        # Log summary
        duration = (end_time - self.start_time).total_seconds()
        logger.info(f"Scraping session {self.session_id} completed:")
        logger.info(f"  Duration: {duration:.2f} seconds")
        logger.info(f"  Total listings found: {self.total_listings_found}")
        logger.info(f"  New listings added: {self.new_listings_added}")
        logger.info(f"  Updated listings: {self.updated_listings}")
        logger.info(f"  Errors: {self.errors_count}")
        logger.info(f"  Reason: {reason}")

# Custom settings for the spider
custom_settings = {
    'ROBOTSTXT_OBEY': True,
    'CONCURRENT_REQUESTS': SCRAPING['concurrent_requests'],
    'DOWNLOAD_DELAY': SCRAPING['download_delay'],
    'RANDOMIZE_DOWNLOAD_DELAY': 0.5,
    'USER_AGENT': random.choice(USER_AGENTS),
    'COOKIES_ENABLED': True,
    'RETRY_TIMES': SCRAPING['retries'],
    'DOWNLOAD_TIMEOUT': SCRAPING['timeout'],
    'LOG_LEVEL': 'INFO',
    'DUPEFILTER_DEBUG': True,
}