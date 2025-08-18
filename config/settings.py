"""
Configuration settings for OLX Car Scraper
"""
import os

# Database settings
DATABASE = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': os.getenv('DB_PORT', 5432),
    'database': os.getenv('DB_NAME', 'olx_cars'),
    'user': os.getenv('DB_USER', os.getenv('USER', 'postgres')),  # Use current user
    'password': os.getenv('DB_PASSWORD', '')  # No password by default for local
}

# Scraping settings
SCRAPING = {
    'base_url': 'https://olx.ba/pretraga?category_id=18',
    'delay_range': (1, 3),  # Random delay in seconds
    'max_pages': 100,       # Safety limit
    'timeout': 30,          # Request timeout
    'retries': 3,           # Retry failed requests
    'concurrent_requests': 1,  # Be respectful to the server
    'download_delay': 2     # Delay between requests
}

# User agents for rotation
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/120.0'
]

# Data validation rules
VALIDATION = {
    'min_price': 500,       # Minimum realistic price in KM
    'max_price': 200000,    # Maximum realistic price in KM
    'min_year': 1990,       # Oldest realistic year
    'max_year': 2025,       # Current year
    'max_mileage': 500000   # Maximum realistic mileage in km
}

# Logging settings
LOGGING = {
    'level': 'INFO',
    'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    'file': 'olx_scraper.log'
}

# Selenium settings
SELENIUM = {
    'headless': True,
    'window_size': (1920, 1080),
    'implicit_wait': 10,
    'page_load_strategy': 'normal'
}

# Dashboard settings
DASHBOARD = {
    'title': 'OLX.ba Car Market Analysis',
    'page_title': 'Car Market Intelligence',
    'layout': 'wide',
    'sidebar_state': 'expanded'
}