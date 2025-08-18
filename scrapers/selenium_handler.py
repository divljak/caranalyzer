"""
Selenium handler for JavaScript-heavy pages on OLX.ba
"""
import time
import random
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import sys
import os

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import SELENIUM, USER_AGENTS, SCRAPING

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SeleniumHandler:
    """Handles JavaScript rendering for OLX.ba pages"""
    
    def __init__(self):
        self.driver = None
        self.wait = None
        
    def setup_driver(self):
        """Setup Chrome driver with optimal settings"""
        try:
            chrome_options = Options()
            
            if SELENIUM['headless']:
                chrome_options.add_argument('--headless')
            
            # Performance and stealth options
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--disable-extensions')
            chrome_options.add_argument('--disable-plugins')
            chrome_options.add_argument('--disable-images')
            chrome_options.add_argument('--disable-javascript-harmony-shipping')
            chrome_options.add_argument('--disable-background-timer-throttling')
            chrome_options.add_argument('--disable-backgrounding-occluded-windows')
            chrome_options.add_argument('--disable-renderer-backgrounding')
            chrome_options.add_argument('--disable-features=TranslateUI')
            
            # Window size
            chrome_options.add_argument(f"--window-size={SELENIUM['window_size'][0]},{SELENIUM['window_size'][1]}")
            
            # Random user agent
            user_agent = random.choice(USER_AGENTS)
            chrome_options.add_argument(f'--user-agent={user_agent}')
            
            # Setup service
            service = Service(ChromeDriverManager().install())
            
            # Set page load strategy before creating driver
            chrome_options.page_load_strategy = SELENIUM.get('page_load_strategy', 'normal')
            
            # Create driver
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            self.driver.implicitly_wait(SELENIUM['implicit_wait'])
            
            self.wait = WebDriverWait(self.driver, 20)
            
            logger.info("Chrome driver setup successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error setting up Chrome driver: {str(e)}")
            return False
    
    def get_page(self, url: str, max_retries: int = 3) -> bool:
        """Load a page with retries"""
        for attempt in range(max_retries):
            try:
                logger.info(f"Loading page: {url} (attempt {attempt + 1})")
                self.driver.get(url)
                
                # Wait for page to load
                time.sleep(random.uniform(2, 4))
                
                # Check if page loaded successfully
                if "olx.ba" in self.driver.current_url.lower():
                    logger.info(f"Page loaded successfully: {self.driver.current_url}")
                    return True
                else:
                    logger.warning(f"Unexpected URL after loading: {self.driver.current_url}")
                    
            except TimeoutException:
                logger.warning(f"Timeout loading page {url} on attempt {attempt + 1}")
            except Exception as e:
                logger.error(f"Error loading page {url} on attempt {attempt + 1}: {str(e)}")
            
            if attempt < max_retries - 1:
                time.sleep(random.uniform(3, 6))
        
        logger.error(f"Failed to load page after {max_retries} attempts: {url}")
        return False
    
    def get_listing_cards(self) -> list:
        """Extract listing cards from search results page"""
        try:
            # Wait for listing cards to load
            time.sleep(3)
            
            # OLX.ba uses .listing-card as the main selector
            listing_cards = self.driver.find_elements(By.CSS_SELECTOR, ".listing-card")
            
            if listing_cards:
                logger.info(f"Found {len(listing_cards)} listing cards using .listing-card selector")
                return listing_cards
            
            # Fallback: try to find listing links directly
            logger.warning("No .listing-card elements found, trying fallback selectors")
            
            fallback_selectors = [
                "a[href*='/artikal/']",  # Direct links to listings
                "[class*='listing-card']",  # Any class containing listing-card
                "[data-v-3561702f][class*='card']",  # Vue.js component with card class
            ]
            
            for selector in fallback_selectors:
                try:
                    cards = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if cards and len(cards) > 10:  # Ensure we have multiple listings
                        logger.info(f"Found {len(cards)} cards using fallback selector: {selector}")
                        return cards
                except Exception as e:
                    continue
            
            # Debug: Save page source to help understand the structure
            try:
                page_source = self.driver.page_source
                with open("debug_page_source.html", "w", encoding="utf-8") as f:
                    f.write(page_source)
                logger.info("Page source saved to debug_page_source.html for analysis")
            except Exception as e:
                logger.error(f"Could not save page source: {str(e)}")
            
            logger.warning("No listing cards found with any selector")
            return []
            
        except Exception as e:
            logger.error(f"Error getting listing cards: {str(e)}")
            return []
    
    def extract_card_data(self, card) -> dict:
        """Extract data from a single listing card"""
        data = {}
        
        try:
            # The .listing-card is inside an <a> tag, so we need to get the parent
            try:
                # Try to get the parent link element
                parent = card.find_element(By.XPATH, "./..")
                if parent.tag_name == 'a' and '/artikal/' in parent.get_attribute("href"):
                    link_element = parent
                else:
                    # The card itself might be the link in some cases
                    if card.tag_name == 'a':
                        link_element = card
                    else:
                        # Find any ancestor that is a link
                        link_element = card.find_element(By.XPATH, "./ancestor::a[contains(@href, '/artikal/')]")
            except NoSuchElementException:
                logger.warning("Could not find parent link element")
                return {}
            
            listing_url = link_element.get_attribute("href")
            if not listing_url or "/artikal/" not in listing_url:
                logger.warning("No valid listing URL found")
                return {}
            
            data['listing_url'] = listing_url
            
            # Extract listing ID from URL
            listing_id = listing_url.split("/artikal/")[1].split("/")[0].split("?")[0]
            data['listing_id'] = listing_id
            
            # Get title (contains make, model, year)
            try:
                # OLX.ba uses .main-heading.normal-heading for titles
                title_element = card.find_element(By.CSS_SELECTOR, ".main-heading.normal-heading")
                title = title_element.text.strip()
                data['title'] = title
                
                # Parse title for make, model, year
                parsed_title = self.parse_title(title)
                data.update(parsed_title)
                
            except NoSuchElementException:
                # Fallback: try to get title from alt attribute of image
                try:
                    img_element = card.find_element(By.CSS_SELECTOR, ".listing-image-main")
                    title = img_element.get_attribute("alt")
                    if title:
                        data['title'] = title
                        parsed_title = self.parse_title(title)
                        data.update(parsed_title)
                except NoSuchElementException:
                    logger.warning("Title not found in listing card")
            
            # Get price
            try:
                # OLX.ba uses .smaller class within .price-wrap for prices
                price_element = card.find_element(By.CSS_SELECTOR, ".price-wrap .smaller")
                price_text = price_element.text.strip()
                data['price'] = self.parse_price(price_text)
            except NoSuchElementException:
                logger.warning("Price not found in listing card")
            
            # Get posting date
            try:
                # Date is in a p tag, usually the last p tag in the card
                date_elements = card.find_elements(By.TAG_NAME, "p")
                if date_elements:
                    # Take the last p element which typically contains the date
                    date_text = date_elements[-1].text.strip()
                    if "prije" in date_text.lower():
                        data['posted_date'] = self.parse_date(date_text)
            except Exception as e:
                logger.warning(f"Error parsing date: {str(e)}")
            
            # Get image URL
            try:
                img_element = card.find_element(By.CSS_SELECTOR, ".listing-image-main")
                data['image_url'] = img_element.get_attribute("src")
            except NoSuchElementException:
                logger.debug("Image not found in listing card")
            
            # Try to extract additional info from the listing title and context
            # OLX.ba doesn't show detailed specs in listing cards, this info is usually in the title
            if 'title' in data:
                additional_info = self.parse_additional_info_from_title(data['title'])
                data.update(additional_info)
            
            return data
            
        except Exception as e:
            logger.error(f"Error extracting card data: {str(e)}")
            return {}
    
    def parse_title(self, title: str) -> dict:
        """Parse car make, model, and year from title"""
        parsed = {}
        
        try:
            # Common patterns for car titles
            title_lower = title.lower()
            
            # Try to extract year (4 digits, usually at the end)
            import re
            year_match = re.search(r'\b(19|20)\d{2}\b', title)
            if year_match:
                parsed['year'] = int(year_match.group())
            
            # Simple make extraction (first word usually)
            words = title.split()
            if words:
                parsed['make'] = words[0].strip(',.')
                
                # Try to get model (words after make, before year)
                if len(words) > 1:
                    model_words = []
                    for word in words[1:]:
                        if not re.match(r'\b(19|20)\d{2}\b', word):
                            model_words.append(word.strip(',.'))
                        else:
                            break
                    if model_words:
                        parsed['model'] = ' '.join(model_words[:3])  # Limit to 3 words
            
        except Exception as e:
            logger.error(f"Error parsing title '{title}': {str(e)}")
        
        return parsed
    
    def parse_price(self, price_text: str) -> int:
        """Parse price from text like '15.500 KM' to integer"""
        try:
            # Handle "Na upit" (On request) prices
            if 'na upit' in price_text.lower() or 'on request' in price_text.lower():
                return None
            
            # Remove currency and formatting
            price_clean = price_text.replace('KM', '').replace('.', '').replace(',', '').strip()
            
            # Extract numbers only
            import re
            numbers = re.findall(r'\d+', price_clean)
            if numbers:
                # Join all numbers (handles cases like "15 500")
                price_str = ''.join(numbers)
                return int(price_str) if price_str.isdigit() else None
            
            return None
        except Exception as e:
            logger.error(f"Error parsing price '{price_text}': {str(e)}")
            return None
    
    def parse_date(self, date_text: str) -> str:
        """Parse date from relative format to actual date"""
        try:
            from datetime import datetime, timedelta
            
            date_text_lower = date_text.lower()
            today = datetime.now().date()
            
            if 'danas' in date_text_lower or 'today' in date_text_lower:
                return today
            elif 'juče' in date_text_lower or 'jučer' in date_text_lower or 'yesterday' in date_text_lower:
                return today - timedelta(days=1)
            elif 'pre' in date_text_lower:
                # Extract number of days
                import re
                numbers = re.findall(r'\d+', date_text)
                if numbers:
                    days_ago = int(numbers[0])
                    return today - timedelta(days=days_ago)
            
            return today  # Default to today if parsing fails
            
        except Exception as e:
            logger.error(f"Error parsing date '{date_text}': {str(e)}")
            return datetime.now().date()
    
    def parse_additional_info_from_title(self, title: str) -> dict:
        """Parse additional info from title since OLX.ba includes specs in titles"""
        parsed = {}
        
        try:
            title_lower = title.lower()
            
            # Parse fuel type from title
            fuel_types = ['benzin', 'dizel', 'diesel', 'hibrid', 'hybrid', 'elektro', 'electric', 'plin', 'gas', 'lpg', 'tdi', 'tdci', 'hdi']
            for fuel in fuel_types:
                if fuel in title_lower:
                    fuel_map = {
                        'benzin': 'petrol',
                        'dizel': 'diesel',
                        'diesel': 'diesel',
                        'tdi': 'diesel',
                        'tdci': 'diesel', 
                        'hdi': 'diesel',
                        'hibrid': 'hybrid',
                        'hybrid': 'hybrid',
                        'elektro': 'electric',
                        'electric': 'electric',
                        'plin': 'gas',
                        'gas': 'gas',
                        'lpg': 'gas'
                    }
                    parsed['fuel_type'] = fuel_map.get(fuel, fuel)
                    break
            
            # Parse transmission from title
            if any(word in title_lower for word in ['automatik', 'automatic', 'dsg', 'cvt', 'tiptronic']):
                parsed['transmission'] = 'automatic'
            elif any(word in title_lower for word in ['manual', 'ručni', 'mjenjač']):
                parsed['transmission'] = 'manual'
            
            # Try to extract engine size (like 2.0, 1.6, etc.)
            import re
            engine_match = re.search(r'\b(\d\.\d)\b', title)
            if engine_match:
                parsed['engine_size'] = float(engine_match.group(1))
            
            # Try to extract mileage if mentioned in title (though rare)
            mileage_match = re.search(r'(\d+)\s*(?:km|kilometres?)', title_lower)
            if mileage_match:
                mileage = int(mileage_match.group(1))
                # Only accept reasonable mileage values (not engine sizes, etc.)
                if mileage > 1000 and mileage < 1000000:
                    parsed['mileage'] = mileage
        
        except Exception as e:
            logger.error(f"Error parsing additional info from title: {str(e)}")
        
        return parsed
    
    def scroll_to_load_more(self):
        """Scroll down to load more listings (if infinite scroll)"""
        try:
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(random.uniform(2, 4))
        except Exception as e:
            logger.error(f"Error scrolling page: {str(e)}")
    
    def check_next_page(self) -> str:
        """Check if there's a next page and return its URL"""
        try:
            next_button = self.driver.find_element(By.CSS_SELECTOR, "a[rel='next']")
            if next_button and next_button.is_enabled():
                return next_button.get_attribute("href")
        except NoSuchElementException:
            logger.info("No next page button found")
        except Exception as e:
            logger.error(f"Error checking next page: {str(e)}")
        
        return None
    
    def close(self):
        """Close the browser"""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Browser closed successfully")
            except Exception as e:
                logger.error(f"Error closing browser: {str(e)}")
    
    def __enter__(self):
        """Context manager entry"""
        if self.setup_driver():
            return self
        else:
            raise Exception("Failed to setup Selenium driver")
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()