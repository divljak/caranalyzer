"""
Data validation utilities for OLX Car Scraper
"""
import re
import logging
from datetime import datetime, date
from typing import Dict, Any, Optional, List
import sys
import os

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import VALIDATION

# Set up logging
logger = logging.getLogger(__name__)

class DataValidator:
    """Validates and cleans scraped car listing data"""
    
    def __init__(self):
        self.validation_rules = VALIDATION
        self.errors = []
        self.warnings = []
    
    def validate_listing(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a complete listing data dict"""
        self.errors = []
        self.warnings = []
        validated_data = {}
        
        # Validate required fields
        required_fields = ['listing_id', 'listing_url']
        for field in required_fields:
            if not self._validate_required_field(data, field):
                return None
            validated_data[field] = data[field]
        
        # Validate optional fields with specific rules
        validated_data.update(self._validate_text_fields(data))
        validated_data.update(self._validate_numeric_fields(data))
        validated_data.update(self._validate_enum_fields(data))
        validated_data.update(self._validate_date_fields(data))
        
        # Set default values for missing fields
        validated_data.update(self._set_defaults(data))
        
        return validated_data
    
    def _validate_required_field(self, data: Dict[str, Any], field: str) -> bool:
        """Validate that a required field exists and has value"""
        if field not in data or not data[field]:
            self.errors.append(f"Missing required field: {field}")
            return False
        return True
    
    def _validate_text_fields(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate text fields like make, model, location"""
        validated = {}
        
        # Validate make
        if 'make' in data and data['make']:
            make = str(data['make']).strip().title()
            if len(make) <= 50 and re.match(r'^[a-zA-Z0-9\s\-]+$', make):
                validated['make'] = make
            else:
                self.warnings.append(f"Invalid make format or too long: {make}")
        
        # Validate model
        if 'model' in data and data['model']:
            model = str(data['model']).strip().title()
            if len(model) <= 100 and re.match(r'^[a-zA-Z0-9\s\-\.]+$', model):
                validated['model'] = model
            else:
                self.warnings.append(f"Invalid model format or too long: {model}")
        
        # Validate location
        if 'location' in data and data['location']:
            location = str(data['location']).strip()
            if len(location) <= 100:
                validated['location'] = location
            else:
                self.warnings.append(f"Location too long: {location}")
        
        # Validate description
        if 'description' in data and data['description']:
            description = str(data['description']).strip()
            if len(description) <= 5000:
                validated['description'] = description
            else:
                validated['description'] = description[:5000]
                self.warnings.append("Description truncated to 5000 characters")
        
        return validated
    
    def _validate_numeric_fields(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate numeric fields like price, year, mileage, views"""
        validated = {}
        
        # Validate year
        if 'year' in data and data['year'] is not None:
            try:
                year = int(data['year'])
                if self.validation_rules['min_year'] <= year <= self.validation_rules['max_year']:
                    validated['year'] = year
                else:
                    self.warnings.append(f"Year {year} outside valid range "
                                       f"({self.validation_rules['min_year']}-{self.validation_rules['max_year']})")
            except (ValueError, TypeError):
                self.warnings.append(f"Invalid year value: {data['year']}")
        
        # Validate price
        if 'price' in data and data['price'] is not None:
            try:
                price = int(data['price'])
                if self.validation_rules['min_price'] <= price <= self.validation_rules['max_price']:
                    validated['price'] = price
                else:
                    self.warnings.append(f"Price {price} outside valid range "
                                       f"({self.validation_rules['min_price']}-{self.validation_rules['max_price']})")
            except (ValueError, TypeError):
                self.warnings.append(f"Invalid price value: {data['price']}")
        
        # Validate mileage
        if 'mileage' in data and data['mileage'] is not None:
            try:
                mileage = int(data['mileage'])
                if 0 <= mileage <= self.validation_rules.get('max_mileage', 500000):
                    validated['mileage'] = mileage
                else:
                    self.warnings.append(f"Mileage {mileage} outside valid range (0-{self.validation_rules.get('max_mileage', 500000)})")
            except (ValueError, TypeError):
                self.warnings.append(f"Invalid mileage value: {data['mileage']}")
        
        # Validate views
        if 'views' in data and data['views'] is not None:
            try:
                views = int(data['views'])
                if views >= 0:
                    validated['views'] = views
                else:
                    validated['views'] = 0
                    self.warnings.append(f"Negative views value corrected to 0: {data['views']}")
            except (ValueError, TypeError):
                validated['views'] = 0
                self.warnings.append(f"Invalid views value set to 0: {data['views']}")
        
        return validated
    
    def _validate_enum_fields(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate enumerated fields like fuel_type, transmission, seller_type"""
        validated = {}
        
        # Validate fuel type
        if 'fuel_type' in data and data['fuel_type']:
            fuel_type = str(data['fuel_type']).lower().strip()
            valid_fuels = ['petrol', 'diesel', 'hybrid', 'electric', 'gas', 'lpg']
            if fuel_type in valid_fuels:
                validated['fuel_type'] = fuel_type
            else:
                self.warnings.append(f"Invalid fuel type: {data['fuel_type']}")
        
        # Validate transmission
        if 'transmission' in data and data['transmission']:
            transmission = str(data['transmission']).lower().strip()
            valid_transmissions = ['manual', 'automatic', 'semi-automatic']
            if transmission in valid_transmissions:
                validated['transmission'] = transmission
            else:
                self.warnings.append(f"Invalid transmission: {data['transmission']}")
        
        # Validate seller type
        if 'seller_type' in data and data['seller_type']:
            seller_type = str(data['seller_type']).lower().strip()
            valid_seller_types = ['individual', 'dealer', 'shop']
            if seller_type in valid_seller_types:
                validated['seller_type'] = seller_type
            else:
                validated['seller_type'] = 'individual'  # Default
                self.warnings.append(f"Invalid seller type, defaulting to 'individual': {data['seller_type']}")
        
        return validated
    
    def _validate_date_fields(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate date fields"""
        validated = {}
        
        # Validate posted_date
        if 'posted_date' in data and data['posted_date']:
            if isinstance(data['posted_date'], date):
                validated['posted_date'] = data['posted_date']
            else:
                try:
                    # Try to parse string date
                    if isinstance(data['posted_date'], str):
                        parsed_date = datetime.strptime(data['posted_date'], '%Y-%m-%d').date()
                        validated['posted_date'] = parsed_date
                    else:
                        self.warnings.append(f"Invalid posted_date format: {data['posted_date']}")
                except ValueError:
                    self.warnings.append(f"Could not parse posted_date: {data['posted_date']}")
        
        return validated
    
    def _set_defaults(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Set default values for missing optional fields"""
        defaults = {
            'views': 0,
            'seller_type': 'individual',
            'is_active': True,
            'scraped_at': datetime.utcnow()
        }
        
        # Only set defaults for fields not already present
        result = {}
        for key, default_value in defaults.items():
            if key not in data:
                result[key] = default_value
        
        # Set posted_date to today if missing
        if 'posted_date' not in data:
            result['posted_date'] = date.today()
        
        return result
    
    def validate_url(self, url: str) -> bool:
        """Validate that URL is from OLX.ba and properly formatted"""
        if not url or not isinstance(url, str):
            return False
        
        url_pattern = r'^https?://(?:www\.)?olx\.ba/artikal/\d+/'
        return bool(re.match(url_pattern, url))
    
    def extract_listing_id_from_url(self, url: str) -> Optional[str]:
        """Extract listing ID from OLX URL"""
        if not self.validate_url(url):
            return None
        
        match = re.search(r'/artikal/(\d+)/', url)
        return match.group(1) if match else None
    
    def is_valid_bosnian_city(self, city: str) -> bool:
        """Check if the city name is a valid Bosnian city (basic validation)"""
        if not city or not isinstance(city, str):
            return False
        
        # List of major Bosnian cities for validation
        major_cities = [
            'sarajevo', 'banja luka', 'tuzla', 'zenica', 'mostar', 'bihać',
            'brčko', 'bijeljina', 'prijedor', 'trebinje', 'doboj', 'cazin',
            'visoko', 'goražde', 'konjic', 'velika kladuša', 'gračanica',
            'gradačac', 'bosanska krupa', 'foča', 'živinice', 'sanski most',
            'orašje', 'novi grad', 'modriča', 'derventa', 'lukavac'
        ]
        
        city_lower = city.lower().strip()
        return any(known_city in city_lower for known_city in major_cities)
    
    def clean_make_model(self, text: str) -> str:
        """Clean and standardize car make/model text"""
        if not text:
            return ""
        
        # Remove extra whitespace and convert to title case
        cleaned = ' '.join(text.strip().split()).title()
        
        # Common make name standardizations
        make_corrections = {
            'Vw': 'Volkswagen',
            'Bmw': 'BMW',
            'Mb': 'Mercedes-Benz',
            'Mercedes': 'Mercedes-Benz',
            'Merc': 'Mercedes-Benz',
            'Alfa': 'Alfa Romeo',
            'Land': 'Land Rover'
        }
        
        for short, full in make_corrections.items():
            if cleaned.startswith(short + ' ') or cleaned == short:
                cleaned = cleaned.replace(short, full, 1)
        
        return cleaned
    
    def get_validation_summary(self) -> Dict[str, List[str]]:
        """Get summary of validation errors and warnings"""
        return {
            'errors': self.errors.copy(),
            'warnings': self.warnings.copy()
        }
    
    def has_errors(self) -> bool:
        """Check if there are validation errors"""
        return len(self.errors) > 0
    
    def has_warnings(self) -> bool:
        """Check if there are validation warnings"""
        return len(self.warnings) > 0

# Create a global instance
validator = DataValidator()