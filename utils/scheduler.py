"""
Scheduler for automated daily scraping
"""
import schedule
import time
import logging
import subprocess
import sys
import os
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import LOGGING

# Set up logging
logging.basicConfig(
    level=getattr(logging, LOGGING['level']),
    format=LOGGING['format'],
    handlers=[
        logging.FileHandler(LOGGING['file']),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class ScrapingScheduler:
    """Manages automated scraping schedule"""
    
    def __init__(self):
        self.project_root = Path(__file__).parent.parent
        self.is_running = False
        self.last_run = None
        self.next_run = None
        
    def run_scraper(self):
        """Execute the scraping process"""
        logger.info("=" * 50)
        logger.info("Starting scheduled scraping session")
        logger.info(f"Time: {datetime.now()}")
        logger.info("=" * 50)
        
        try:
            # Change to project directory
            os.chdir(self.project_root)
            
            # Run the main scraper
            result = subprocess.run([
                sys.executable, 'run_scraper.py'
            ], capture_output=True, text=True, timeout=3600)  # 1 hour timeout
            
            if result.returncode == 0:
                logger.info("Scraping completed successfully")
                logger.info(f"Output: {result.stdout}")
                self.last_run = datetime.now()
            else:
                logger.error(f"Scraping failed with return code {result.returncode}")
                logger.error(f"Error output: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            logger.error("Scraping process timed out after 1 hour")
        except Exception as e:
            logger.error(f"Error running scraper: {str(e)}")
        
        logger.info("Scheduled scraping session completed")
        logger.info("=" * 50)
    
    def setup_daily_schedule(self, time_str: str = "06:00"):
        """Setup daily scraping schedule"""
        logger.info(f"Setting up daily scraping at {time_str}")
        
        schedule.every().day.at(time_str).do(self.run_scraper)
        
        # Calculate next run time for logging
        self.next_run = schedule.next_run()
        logger.info(f"Next scheduled run: {self.next_run}")
    
    def setup_test_schedule(self, minutes: int = 5):
        """Setup test schedule (every N minutes) for development"""
        logger.info(f"Setting up test scraping every {minutes} minutes")
        
        schedule.every(minutes).minutes.do(self.run_scraper)
        
        self.next_run = schedule.next_run()
        logger.info(f"Next scheduled run: {self.next_run}")
    
    def run_scheduler(self):
        """Run the scheduler loop"""
        logger.info("Starting scheduler...")
        logger.info("Press Ctrl+C to stop")
        
        self.is_running = True
        
        try:
            while self.is_running:
                schedule.run_pending()
                time.sleep(60)  # Check every minute
                
                # Update next run time
                next_run = schedule.next_run()
                if next_run != self.next_run:
                    self.next_run = next_run
                    logger.info(f"Next scheduled run: {self.next_run}")
                    
        except KeyboardInterrupt:
            logger.info("Scheduler stopped by user")
        except Exception as e:
            logger.error(f"Scheduler error: {str(e)}")
        finally:
            self.is_running = False
            logger.info("Scheduler stopped")
    
    def stop_scheduler(self):
        """Stop the scheduler"""
        self.is_running = False
        schedule.clear()
        logger.info("Scheduler stopped and cleared")
    
    def get_status(self) -> dict:
        """Get scheduler status"""
        return {
            'is_running': self.is_running,
            'last_run': self.last_run,
            'next_run': self.next_run,
            'scheduled_jobs': len(schedule.jobs)
        }
    
    def run_manual_scraping(self):
        """Run scraping manually (for testing)"""
        logger.info("Running manual scraping session")
        self.run_scraper()

def main():
    """Main function for running scheduler"""
    import argparse
    
    parser = argparse.ArgumentParser(description='OLX Car Scraper Scheduler')
    parser.add_argument('--mode', choices=['daily', 'test', 'manual'], default='daily',
                       help='Scheduler mode: daily (06:00), test (every 5 min), or manual (run once)')
    parser.add_argument('--time', default='06:00', 
                       help='Time for daily schedule (HH:MM format)')
    parser.add_argument('--interval', type=int, default=5,
                       help='Interval in minutes for test mode')
    
    args = parser.parse_args()
    
    scheduler = ScrapingScheduler()
    
    if args.mode == 'manual':
        # Run once manually
        scheduler.run_manual_scraping()
    elif args.mode == 'daily':
        # Setup daily schedule
        scheduler.setup_daily_schedule(args.time)
        scheduler.run_scheduler()
    elif args.mode == 'test':
        # Setup test schedule
        scheduler.setup_test_schedule(args.interval)
        scheduler.run_scheduler()

if __name__ == "__main__":
    main()