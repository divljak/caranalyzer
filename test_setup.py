#!/usr/bin/env python3
"""
Test script to verify OLX Car Scraper setup
"""
import sys
import os
from pathlib import Path

# Add current directory to Python path
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

def test_imports():
    """Test that all required modules can be imported"""
    print("Testing imports...")
    
    try:
        from config.settings import DATABASE, SCRAPING, VALIDATION
        print("✅ Config settings imported successfully")
    except ImportError as e:
        print(f"❌ Config import failed: {e}")
        return False
    
    try:
        from database.models import CarListing, ScrapingLog, create_tables
        print("✅ Database models imported successfully")
    except ImportError as e:
        print(f"❌ Database models import failed: {e}")
        return False
    
    try:
        from database.db_manager import db_manager
        print("✅ Database manager imported successfully")
    except ImportError as e:
        print(f"❌ Database manager import failed: {e}")
        return False
    
    try:
        from scrapers.selenium_handler import SeleniumHandler
        print("✅ Selenium handler imported successfully")
    except ImportError as e:
        print(f"❌ Selenium handler import failed: {e}")
        return False
    
    try:
        from scrapers.olx_spider import OLXCarSpider
        print("✅ OLX spider imported successfully")
    except ImportError as e:
        print(f"❌ OLX spider import failed: {e}")
        return False
    
    try:
        from utils.data_validator import validator
        print("✅ Data validator imported successfully")
    except ImportError as e:
        print(f"❌ Data validator import failed: {e}")
        return False
    
    try:
        from utils.scheduler import ScrapingScheduler
        print("✅ Scheduler imported successfully")
    except ImportError as e:
        print(f"❌ Scheduler import failed: {e}")
        return False
    
    return True

def test_dependencies():
    """Test that all required dependencies are installed"""
    print("\nTesting dependencies...")
    
    required_packages = [
        'scrapy', 'selenium', 'streamlit', 'psycopg2', 'pandas', 
        'plotly', 'schedule', 'beautifulsoup4', 'requests', 
        'dateutil', 'webdriver_manager', 'sqlalchemy'
    ]
    
    missing_packages = []
    
    for package in required_packages:
        try:
            if package == 'dateutil':
                import dateutil
            elif package == 'beautifulsoup4':
                import bs4
            elif package == 'webdriver_manager':
                import webdriver_manager
            else:
                __import__(package)
            print(f"✅ {package} is installed")
        except ImportError:
            print(f"❌ {package} is missing")
            missing_packages.append(package)
    
    if missing_packages:
        print(f"\n⚠️  Missing packages: {', '.join(missing_packages)}")
        print("Run: pip install -r requirements.txt")
        return False
    
    return True

def test_configuration():
    """Test configuration settings"""
    print("\nTesting configuration...")
    
    try:
        from config.settings import DATABASE, SCRAPING, VALIDATION, USER_AGENTS
        
        # Check database config
        required_db_keys = ['host', 'port', 'database', 'user', 'password']
        for key in required_db_keys:
            if key not in DATABASE:
                print(f"❌ Missing database config: {key}")
                return False
        print("✅ Database configuration is complete")
        
        # Check scraping config
        required_scraping_keys = ['base_url', 'delay_range', 'max_pages', 'timeout']
        for key in required_scraping_keys:
            if key not in SCRAPING:
                print(f"❌ Missing scraping config: {key}")
                return False
        print("✅ Scraping configuration is complete")
        
        # Check validation config
        required_validation_keys = ['min_price', 'max_price', 'min_year', 'max_year']
        for key in required_validation_keys:
            if key not in VALIDATION:
                print(f"❌ Missing validation config: {key}")
                return False
        print("✅ Validation configuration is complete")
        
        # Check user agents
        if not USER_AGENTS or len(USER_AGENTS) < 3:
            print("❌ Need at least 3 user agents")
            return False
        print("✅ User agents configuration is complete")
        
        return True
        
    except Exception as e:
        print(f"❌ Configuration test failed: {e}")
        return False

def test_database_connection():
    """Test database connection"""
    print("\nTesting database connection...")
    
    try:
        from database.models import create_engine_and_session
        
        engine, SessionLocal = create_engine_and_session()
        session = SessionLocal()
        
        # Test connection
        from sqlalchemy import text
        session.execute(text("SELECT 1"))
        session.close()
        
        print("✅ Database connection successful")
        return True
        
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        print("Make sure PostgreSQL is running and database credentials are correct")
        return False

def test_selenium_setup():
    """Test Selenium WebDriver setup"""
    print("\nTesting Selenium setup...")
    
    try:
        from scrapers.selenium_handler import SeleniumHandler
        
        handler = SeleniumHandler()
        if handler.setup_driver():
            print("✅ Selenium WebDriver setup successful")
            handler.close()
            return True
        else:
            print("❌ Selenium WebDriver setup failed")
            return False
            
    except Exception as e:
        print(f"❌ Selenium setup failed: {e}")
        print("Make sure Chrome browser is installed")
        return False

def main():
    """Run all tests"""
    print("🚗 OLX Car Scraper Setup Test")
    print("=" * 40)
    
    tests = [
        ("Imports", test_imports),
        ("Dependencies", test_dependencies), 
        ("Configuration", test_configuration),
        ("Database Connection", test_database_connection),
        ("Selenium Setup", test_selenium_setup)
    ]
    
    results = []
    
    for test_name, test_func in tests:
        print(f"\n🔍 Running {test_name} test...")
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ {test_name} test crashed: {e}")
            results.append((test_name, False))
    
    # Summary
    print("\n" + "=" * 40)
    print("📊 TEST SUMMARY")
    print("=" * 40)
    
    passed = 0
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{test_name:.<25} {status}")
        if result:
            passed += 1
    
    print(f"\nTests passed: {passed}/{len(results)}")
    
    if passed == len(results):
        print("\n🎉 All tests passed! Setup is complete.")
        print("\nNext steps:")
        print("1. Run: python run_scraper.py --setup-db")
        print("2. Run: python run_scraper.py")
        print("3. Run: streamlit run dashboard/streamlit_app.py")
    else:
        print("\n⚠️  Some tests failed. Please fix the issues above.")
        sys.exit(1)

if __name__ == "__main__":
    main()