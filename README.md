# 🚗 OLX.ba Car Market Analysis MVP

A comprehensive car market intelligence tool that scrapes OLX.ba daily to track Bosnia & Herzegovina's used car market. Provides market insights, pricing trends, and inventory tracking for car dealerships and market analysts.

## 📋 Features

- **Daily Data Collection**: Automated scraping of all passenger car listings from OLX.ba
- **Modern React Dashboard**: Interactive web dashboard with market trends and insights
- **Price Analysis**: Advanced filtering and price comparison tools
- **Data Export**: CSV export functionality for further analysis
- **Automated Operation**: Scheduled daily scraping without manual intervention

### Key Business Questions Answered
- Which car models are selling fastest?
- What's the current market price for any make/model/year combination?
- What new inventory appeared today?
- Which models have the most competition/listings?

## 🛠️ Technology Stack

- **Scraping**: Scrapy + Selenium for JavaScript handling
- **Database**: PostgreSQL with SQLAlchemy ORM
- **Frontend**: React + TypeScript with modern UI components
- **Backend API**: FastAPI with automatic documentation
- **Scheduling**: Python `schedule` library
- **Language**: Python 3.8+ and TypeScript

## 📁 Project Structure

```
olx-car-scraper/
├── scrapers/
│   ├── __init__.py
│   ├── olx_spider.py          # Main Scrapy spider
│   └── selenium_handler.py    # JavaScript rendering
├── database/
│   ├── __init__.py
│   ├── models.py             # Database schema
│   └── db_manager.py         # Database operations
├── frontend/                  # React TypeScript dashboard
│   ├── src/
│   ├── package.json
│   └── vite.config.ts
├── api/                      # FastAPI backend
│   ├── main.py
│   └── requirements.txt
├── utils/
│   ├── __init__.py
│   ├── scheduler.py          # Automated running
│   └── data_validator.py     # Data quality checks
├── config/
│   └── settings.py           # Configuration
├── requirements.txt
├── README.md
└── run_scraper.py           # Main entry point
```

## 🚀 Quick Start

### Prerequisites

1. **Python 3.8+**
2. **PostgreSQL** (local installation)
3. **Chrome Browser** (for Selenium WebDriver)

### Installation

1. **Clone/Download the project:**
   ```bash
   cd "path/to/olx scraper"
   ```

2. **Create virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Setup PostgreSQL database:**
   ```sql
   CREATE DATABASE olx_cars;
   CREATE USER olx_user WITH PASSWORD 'your_password';
   GRANT ALL PRIVILEGES ON DATABASE olx_cars TO olx_user;
   ```

5. **Configure database settings:**
   Edit `config/settings.py` or set environment variables:
   ```bash
   export DB_HOST=localhost
   export DB_PORT=5432
   export DB_NAME=olx_cars
   export DB_USER=olx_user
   export DB_PASSWORD=your_password
   ```

6. **Setup database tables:**
   ```bash
   python run_scraper.py --setup-db
   ```

### Running the Application

#### 1. Manual Scraping (Test)
```bash
python run_scraper.py
```

#### 2. Start All Services
```bash
./start_all.sh
```
This will start:
- API Backend: http://localhost:8000
- React Dashboard: http://localhost:8080
- API Documentation: http://localhost:8000/docs

#### 3. Automated Daily Scraping
```bash
# Run daily at 6 AM
python utils/scheduler.py --mode daily

# Test mode (every 5 minutes)
python utils/scheduler.py --mode test --interval 5

# Manual single run
python utils/scheduler.py --mode manual
```

## 📊 Dashboard Features

### 📈 Market Overview
- Total active listings and new listings today
- Average market price and most viewed car
- Top makes and models by listing count
- Interactive charts and metrics

### 🔥 Hot Cars
- Cars with highest views in the last 7 days
- Filterable by views count and days since posted
- Export functionality

### 🆕 Today's New Listings
- All cars posted in the last 24 hours
- Price distribution analysis
- Advanced filtering by price, location, fuel type
- Real-time updates

### 🔍 Price Analysis Tool
- Interactive search by make, model, year, mileage
- Price distribution histograms
- Price vs year trend analysis
- Detailed results table with export

## ⚙️ Configuration

### Database Settings
```python
DATABASE = {
    'host': 'localhost',
    'port': 5432,
    'database': 'olx_cars',
    'user': 'your_username',
    'password': 'your_password'
}
```

### Scraping Settings
```python
SCRAPING = {
    'base_url': 'https://olx.ba/pretraga?category_id=18',
    'delay_range': (1, 3),  # Random delay in seconds
    'max_pages': 100,       # Safety limit
    'concurrent_requests': 1,  # Be respectful
    'download_delay': 2     # Delay between requests
}
```

### Validation Rules
```python
VALIDATION = {
    'min_price': 500,       # Minimum realistic price in KM
    'max_price': 200000,    # Maximum realistic price in KM
    'min_year': 1990,       # Oldest realistic year
    'max_year': 2025        # Current year
}
```

## 📚 Usage Examples

### Command Line Options

```bash
# Setup database only
python run_scraper.py --setup-db

# Show statistics only
python run_scraper.py --stats --no-scrape

# Clean up old data (30 days)
python run_scraper.py --cleanup 30

# Full run with statistics
python run_scraper.py
```

### Programmatic Usage

```python
from database.db_manager import db_manager

# Get market statistics
stats = db_manager.get_market_stats()
print(f"Total listings: {stats['total_active']}")

# Search for specific cars
cars = db_manager.search_cars(make='BMW', year_min=2015)
print(f"Found {len(cars)} BMW cars from 2015+")

# Get today's listings
new_cars = db_manager.get_todays_listings()
print(f"New listings today: {len(new_cars)}")
```

## 🔧 Advanced Configuration

### Environment Variables
```bash
# Database
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=olx_cars
export DB_USER=postgres
export DB_PASSWORD=password

# Optional: Custom settings
export SCRAPING_DELAY=2
export MAX_PAGES=50
```

### Custom User Agents
Add more user agents in `config/settings.py`:
```python
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36...',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36...',
    # Add your custom user agents here
]
```

## 📈 Database Schema

### Main Table: car_listings
```sql
CREATE TABLE car_listings (
    listing_id VARCHAR(50) PRIMARY KEY,
    make VARCHAR(50) NOT NULL,
    model VARCHAR(100) NOT NULL,
    year INTEGER,
    price INTEGER,              -- Price in Bosnian Marks (KM)
    mileage INTEGER,           -- In kilometers
    views INTEGER DEFAULT 0,
    posted_date DATE,
    location VARCHAR(100),     -- City/region in Bosnia
    seller_type VARCHAR(20),   -- 'individual' or 'dealer'
    fuel_type VARCHAR(20),     -- 'petrol', 'diesel', 'hybrid', 'electric'
    transmission VARCHAR(20),   -- 'manual', 'automatic'
    listing_url VARCHAR(500),
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);
```

### Indexes for Performance
```sql
CREATE INDEX idx_make_model ON car_listings(make, model);
CREATE INDEX idx_posted_date ON car_listings(posted_date);
CREATE INDEX idx_price ON car_listings(price);
CREATE INDEX idx_scraped_at ON car_listings(scraped_at);
```

## 🔍 Troubleshooting

### Common Issues

1. **Chrome WebDriver Issues**
   ```bash
   # Update webdriver
   pip install --upgrade webdriver-manager
   ```

2. **Database Connection Errors**
   ```bash
   # Check PostgreSQL service
   brew services start postgresql  # macOS
   sudo systemctl start postgresql  # Linux
   ```

3. **Selenium TimeoutException**
   - Check internet connection
   - Verify OLX.ba is accessible
   - Increase timeout in `config/settings.py`

4. **Empty Results**
   - Check if OLX.ba changed their HTML structure
   - Verify CSS selectors in `selenium_handler.py`
   - Check logs for specific errors

### Debugging

Enable debug mode:
```python
# In config/settings.py
LOGGING = {
    'level': 'DEBUG',  # Change from INFO to DEBUG
    'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    'file': 'olx_scraper.log'
}
```

View logs:
```bash
tail -f olx_scraper.log
```

## 📋 Performance Tips

1. **Database Optimization**
   - Regularly run `VACUUM ANALYZE` on PostgreSQL
   - Monitor query performance with `EXPLAIN`
   - Consider connection pooling for high-load scenarios

2. **Scraping Performance**
   - Adjust `download_delay` based on your internet speed
   - Use `concurrent_requests: 1` to be respectful to OLX.ba
   - Monitor for rate limiting responses

3. **Frontend Performance**
   - React components with optimized rendering
   - API data caching and efficient state management
   - Responsive design for all device sizes
   - Fast Vite development server

## 🚨 Legal & Ethical Compliance

- ✅ Respects robots.txt file
- ✅ Implements proper rate limiting (1-3 second delays)
- ✅ Does not store personal information
- ✅ Uses data only for market analysis
- ⚠️ Monitor OLX.ba terms of service for changes

## 📊 Success Metrics

- **Data Quality**: >95% of listings have all required fields
- **Coverage**: Scrape >90% of available listings daily  
- **Performance**: Dashboard loads in <5 seconds
- **Reliability**: <1% failure rate in daily scraping

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📄 License

This project is for educational and market research purposes only. Please comply with OLX.ba's terms of service and applicable laws.

## 📞 Support

For issues and questions:
1. Check the troubleshooting section above
2. Review the logs in `olx_scraper.log`
3. Ensure all dependencies are correctly installed
4. Verify database connectivity

---

**Built with ❤️ for Bosnia & Herzegovina's automotive market analysis**