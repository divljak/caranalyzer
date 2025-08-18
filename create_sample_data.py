#!/usr/bin/env python3
"""
Create sample data for testing the OLX Car Scraper dashboard
"""
import sys
import os
from pathlib import Path
from datetime import datetime, date, timedelta
import random

# Add current directory to Python path
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

from database.db_manager import db_manager

# Sample car data
CAR_MAKES = ['BMW', 'Mercedes-Benz', 'Audi', 'Volkswagen', 'Opel', 'Ford', 'Peugeot', 'Renault', 'Skoda', 'Toyota']
CAR_MODELS = {
    'BMW': ['320d', '520d', 'X3', 'X5', '318i', '525d'],
    'Mercedes-Benz': ['C-Class', 'E-Class', 'A-Class', 'ML', 'GLK', 'CLA'],
    'Audi': ['A4', 'A6', 'Q5', 'Q7', 'A3', 'A8'],
    'Volkswagen': ['Golf', 'Passat', 'Tiguan', 'Polo', 'Jetta', 'Touran'],
    'Opel': ['Astra', 'Insignia', 'Corsa', 'Mokka', 'Vectra', 'Zafira'],
    'Ford': ['Focus', 'Mondeo', 'Fiesta', 'Kuga', 'C-Max', 'Galaxy'],
    'Peugeot': ['308', '508', '307', '206', '207', '3008'],
    'Renault': ['Megane', 'Clio', 'Laguna', 'Scenic', 'Captur', 'Kadjar'],
    'Skoda': ['Octavia', 'Fabia', 'Superb', 'Rapid', 'Yeti', 'Kodiaq'],
    'Toyota': ['Corolla', 'Avensis', 'RAV4', 'Yaris', 'Auris', 'Land Cruiser']
}

LOCATIONS = ['Sarajevo', 'Banja Luka', 'Tuzla', 'Zenica', 'Mostar', 'Bihać', 'Brčko', 'Bijeljina', 'Prijedor', 'Trebinje']
FUEL_TYPES = ['diesel', 'petrol', 'hybrid', 'electric']
TRANSMISSIONS = ['manual', 'automatic']
SELLER_TYPES = ['individual', 'dealer']

def create_sample_listings(count: int = 100):
    """Create sample car listings"""
    print(f"Creating {count} sample car listings...")
    
    for i in range(count):
        # Random car details
        make = random.choice(CAR_MAKES)
        model = random.choice(CAR_MODELS[make])
        year = random.randint(2005, 2024)
        price = random.randint(3000, 50000)
        mileage = random.randint(50000, 400000)
        views = random.randint(0, 500)
        
        # Random dates (mostly recent, some older)
        days_ago = random.choices(
            range(0, 30), 
            weights=[10 if d < 7 else 5 if d < 14 else 1 for d in range(30)]
        )[0]
        posted_date = date.today() - timedelta(days=days_ago)
        
        # Other details
        location = random.choice(LOCATIONS)
        fuel_type = random.choice(FUEL_TYPES)
        transmission = random.choice(TRANSMISSIONS)
        seller_type = random.choice(SELLER_TYPES)
        
        # Create listing data
        listing_data = {
            'listing_id': f'sample_{i+1:04d}',
            'make': make,
            'model': model,
            'year': year,
            'price': price,
            'mileage': mileage,
            'views': views,
            'posted_date': posted_date,
            'location': location,
            'fuel_type': fuel_type,
            'transmission': transmission,
            'seller_type': seller_type,
            'listing_url': f'https://olx.ba/artikal/sample_{i+1:04d}/',
            'description': f'{make} {model} {year}. godina, {mileage:,} km, {fuel_type}, {transmission}',
            'scraped_at': datetime.utcnow(),
            'is_active': True
        }
        
        # Add to database
        db_manager.add_or_update_listing(listing_data)
        
        if (i + 1) % 20 == 0:
            print(f"Created {i + 1} listings...")
    
    print(f"Successfully created {count} sample listings!")

def create_extra_todays_listings(count: int = 15):
    """Create extra listings for today to test 'new today' functionality"""
    print(f"Creating {count} additional listings for today...")
    
    for i in range(count):
        make = random.choice(CAR_MAKES)
        model = random.choice(CAR_MODELS[make])
        year = random.randint(2010, 2024)
        price = random.randint(5000, 80000)
        mileage = random.randint(30000, 300000)
        views = random.randint(5, 150)  # Today's listings have fewer views
        
        listing_data = {
            'listing_id': f'today_{i+1:04d}',
            'make': make,
            'model': model,
            'year': year,
            'price': price,
            'mileage': mileage,
            'views': views,
            'posted_date': date.today(),  # Today
            'location': random.choice(LOCATIONS),
            'fuel_type': random.choice(FUEL_TYPES),
            'transmission': random.choice(TRANSMISSIONS),
            'seller_type': random.choice(SELLER_TYPES),
            'listing_url': f'https://olx.ba/artikal/today_{i+1:04d}/',
            'description': f'{make} {model} {year} - Nova objava danas!',
            'scraped_at': datetime.utcnow(),
            'is_active': True
        }
        
        db_manager.add_or_update_listing(listing_data)
    
    print(f"Successfully created {count} today's listings!")

def create_hot_cars(count: int = 10):
    """Create some high-view cars for the hot cars section"""
    print(f"Creating {count} hot cars with high views...")
    
    for i in range(count):
        make = random.choice(CAR_MAKES[:5])  # Popular brands
        model = random.choice(CAR_MODELS[make])
        year = random.randint(2015, 2024)  # Newer cars
        price = random.randint(15000, 100000)  # Higher prices
        mileage = random.randint(20000, 200000)
        views = random.randint(200, 1000)  # High views
        
        # Posted in last week
        days_ago = random.randint(1, 7)
        posted_date = date.today() - timedelta(days=days_ago)
        
        listing_data = {
            'listing_id': f'hot_{i+1:04d}',
            'make': make,
            'model': model,
            'year': year,
            'price': price,
            'mileage': mileage,
            'views': views,
            'posted_date': posted_date,
            'location': random.choice(LOCATIONS[:3]),  # Major cities
            'fuel_type': random.choice(FUEL_TYPES),
            'transmission': random.choice(TRANSMISSIONS),
            'seller_type': 'dealer' if random.random() > 0.3 else 'individual',
            'listing_url': f'https://olx.ba/artikal/hot_{i+1:04d}/',
            'description': f'🔥 POPULARAN: {make} {model} {year} - {views} pregleda!',
            'scraped_at': datetime.utcnow(),
            'is_active': True
        }
        
        db_manager.add_or_update_listing(listing_data)
    
    print(f"Successfully created {count} hot cars!")

def main():
    """Create all sample data"""
    print("🚗 Creating Sample Data for OLX Car Scraper")
    print("=" * 50)
    
    # Create main sample data
    create_sample_listings(100)
    
    # Create today's listings
    create_extra_todays_listings(15)
    
    # Create hot cars
    create_hot_cars(10)
    
    # Show final statistics
    print("\n" + "=" * 50)
    print("📊 FINAL DATABASE STATISTICS")
    print("=" * 50)
    
    stats = db_manager.get_market_stats()
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
    
    print("\nSample data creation completed! 🎉")
    print("You can now run the dashboard: streamlit run dashboard/streamlit_app.py")

if __name__ == "__main__":
    main()