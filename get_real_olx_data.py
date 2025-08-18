#!/usr/bin/env python3
"""
Quick script to get real OLX.ba data for dashboard testing
"""
import sys
import os
from datetime import datetime, date
import re

# Add current directory to Python path
sys.path.insert(0, '.')

from scrapers.selenium_handler import SeleniumHandler
from database.db_manager import db_manager

def extract_make_model_from_title(title):
    """Extract make and model from title"""
    # Common car makes
    makes = [
        'Volkswagen', 'BMW', 'Mercedes', 'Audi', 'Opel', 'Ford', 'Skoda', 
        'Renault', 'Peugeot', 'Toyota', 'Honda', 'Nissan', 'Hyundai', 'Kia',
        'Fiat', 'Citroen', 'Seat', 'Mazda', 'Volvo', 'Saab', 'Mitsubishi',
        'Subaru', 'Suzuki', 'Dacia', 'Alfa Romeo'
    ]
    
    title_upper = title.upper()
    
    # Find make
    make_found = None
    for make in makes:
        if make.upper() in title_upper:
            make_found = make
            break
    
    if not make_found:
        # Try first word as make
        words = title.split()
        if words:
            make_found = words[0]
    
    # Extract model (word after make)
    model_found = None
    if make_found:
        words = title.split()
        try:
            make_index = next(i for i, word in enumerate(words) if make_found.upper() in word.upper())
            if make_index + 1 < len(words):
                model_found = words[make_index + 1]
        except:
            pass
    
    return make_found, model_found

def extract_year_from_title(title):
    """Extract year from title"""
    # Look for 4-digit year between 1990-2025
    year_match = re.search(r'\b(19[9]\d|20[0-2]\d)\b', title)
    if year_match:
        year = int(year_match.group(1))
        if 1990 <= year <= 2025:
            return year
    return None

def clean_price(price_text):
    """Extract numeric price from text"""
    if not price_text or 'upit' in price_text.lower():
        return None
    
    # Extract numbers
    numbers = re.findall(r'\d+', price_text.replace(',', '').replace('.', ''))
    if numbers:
        return int(''.join(numbers))
    return None

def get_real_olx_data(max_listings=50):
    """Get real data from OLX.ba"""
    print("🚗 Getting Real OLX.ba Car Data")
    print("=" * 40)
    
    selenium_handler = SeleniumHandler()
    
    try:
        # Setup driver
        if not selenium_handler.setup_driver():
            print("❌ Failed to setup Selenium driver")
            return
        
        # Load OLX cars page
        url = "https://olx.ba/pretraga?category_id=18"
        print(f"📡 Loading: {url}")
        
        if not selenium_handler.get_page(url):
            print("❌ Failed to load page")
            return
        
        # Get listing cards
        listing_cards = selenium_handler.get_listing_cards()
        print(f"📋 Found {len(listing_cards)} listing cards")
        
        if not listing_cards:
            print("❌ No listing cards found")
            return
        
        processed = 0
        
        for i, card in enumerate(listing_cards[:max_listings], 1):
            try:
                print(f"\n🔍 Processing listing {i}/{min(max_listings, len(listing_cards))}")
                
                # Extract data using selenium handler
                raw_data = selenium_handler.extract_card_data(card)
                
                if not raw_data:
                    print(f"   ⚠️ No data extracted from card {i}")
                    continue
                
                print(f"   📄 Title: {raw_data.get('title', 'N/A')}")
                print(f"   💰 Price: {raw_data.get('price', 'N/A')}")
                print(f"   🔗 URL: {raw_data.get('listing_url', 'N/A')}")
                
                # Process the data
                make, model = extract_make_model_from_title(raw_data.get('title', ''))
                year = extract_year_from_title(raw_data.get('title', ''))
                price = clean_price(raw_data.get('price', ''))
                
                # Create cleaned listing data
                listing_data = {
                    'listing_id': raw_data.get('listing_id', f'olx_real_{i}'),
                    'listing_url': raw_data.get('listing_url', ''),
                    'make': make,
                    'model': model,
                    'year': year,
                    'price': price,
                    'views': 0,  # OLX.ba doesn't show view counts publicly
                    'posted_date': date.today(),  # Use today since we can't get exact date easily
                    'location': 'Bosnia and Herzegovina',
                    'fuel_type': None,
                    'transmission': None,
                    'seller_type': 'individual',
                    'description': raw_data.get('title', ''),
                    'scraped_at': datetime.utcnow(),
                    'is_active': True
                }
                
                # Save to database
                try:
                    is_new = db_manager.add_or_update_listing(listing_data)
                    if is_new:
                        print(f"   ✅ Added new listing: {make} {model}")
                        processed += 1
                    else:
                        print(f"   🔄 Updated existing listing: {make} {model}")
                        processed += 1
                except Exception as e:
                    print(f"   ❌ Database error: {e}")
                
            except Exception as e:
                print(f"   ❌ Error processing card {i}: {e}")
        
        print(f"\n🎉 Successfully processed {processed} real OLX listings!")
        
    finally:
        selenium_handler.close()

if __name__ == "__main__":
    get_real_olx_data(20)  # Get 20 real listings