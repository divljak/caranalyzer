"""
Scrapy settings for OLX Car Scraper
"""
import sys
import os
import random

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import SCRAPING, USER_AGENTS

# Scrapy settings for olx_scraper project
BOT_NAME = 'olx_scraper'

SPIDER_MODULES = ['scrapers']
NEWSPIDER_MODULE = 'scrapers'

# Obey robots.txt rules
ROBOTSTXT_OBEY = True

# Configure delays
DOWNLOAD_DELAY = SCRAPING['download_delay']
RANDOMIZE_DOWNLOAD_DELAY = 0.5

# Configure concurrent requests
CONCURRENT_REQUESTS = SCRAPING['concurrent_requests']
CONCURRENT_REQUESTS_PER_DOMAIN = 1

# Configure user agent
USER_AGENT = random.choice(USER_AGENTS)

# Enable cookies
COOKIES_ENABLED = True

# Configure retries
RETRY_TIMES = SCRAPING['retries']
DOWNLOAD_TIMEOUT = SCRAPING['timeout']

# Configure logging
LOG_LEVEL = 'INFO'

# Enable and configure the AutoThrottle extension
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1
AUTOTHROTTLE_MAX_DELAY = 10
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
AUTOTHROTTLE_DEBUG = False

# Enable duplicate filter
DUPEFILTER_DEBUG = True

# Configure pipelines
ITEM_PIPELINES = {
    # Add custom pipelines here if needed
}

# Configure downloader middlewares
DOWNLOADER_MIDDLEWARES = {
    'scrapy.downloadermiddlewares.useragent.UserAgentMiddleware': None,
    'scrapy.downloadermiddlewares.retry.RetryMiddleware': 90,
}

# Configure extensions
EXTENSIONS = {
    'scrapy.extensions.telnet.TelnetConsole': None,
}

# Configure request fingerprinting
REQUEST_FINGERPRINTER_IMPLEMENTATION = '2.7'

# Disable telnet console
TELNETCONSOLE_ENABLED = False

# Configure feed exports
FEED_EXPORT_ENCODING = 'utf-8'