"""
FastAPI Backend for Car Flipper Dashboard
Serves car market analysis data via REST API
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, date, timedelta
import sys
import os
import urllib.parse
from collections import Counter, defaultdict
import subprocess
import asyncio
from pathlib import Path

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from database.db_manager import db_manager
    from database.models import create_engine_and_session, CarListing
    from sqlalchemy import desc, func, and_
except ImportError as e:
    print(f"Import error: {e}")
    print("Make sure you're running from the project root directory")
    print("Current working directory:", os.getcwd())
    raise

app = FastAPI(
    title="Car Flipper Dashboard API",
    description="REST API for car market analysis and flipping intelligence",
    version="1.0.0"
)

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", 
        "http://127.0.0.1:3000",
        "http://localhost:8080", 
        "http://127.0.0.1:8080"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models for API responses
class CarListingResponse(BaseModel):
    listing_id: str
    make: str
    model: str
    year: Optional[int]
    price: Optional[int]
    mileage: Optional[int]
    views: Optional[int]
    posted_date: Optional[date]
    location: Optional[str]
    fuel_type: Optional[str]
    transmission: Optional[str]

class OpportunityCard(BaseModel):
    type: str  # "hot_flip", "overpriced", "rising_demand"
    title: str
    subtitle: str
    model: Optional[str]
    days: Optional[float]
    color: str  # "green", "orange", "blue"

class PriceBracketModel(BaseModel):
    rank: int
    model: str
    days: float
    sample_url: Optional[str] = None
    sample_price: Optional[int] = None

class PriceBracket(BaseModel):
    bracket_name: str
    models: List[PriceBracketModel]
    total_cars: int
    total_models: int

class FastestSellingModel(BaseModel):
    model: str
    year: int
    avg_days_on_market: float
    avg_price: float
    demand_level: str

class TrendingModel(BaseModel):
    model: str
    trend_text: str
    trend_type: str  # "views_up", "days_down", "general"

class ScatterPoint(BaseModel):
    price: float
    days_on_market: float
    mileage_bracket: str
    model: str
    views: int

class DashboardData(BaseModel):
    opportunities: List[OpportunityCard]
    price_brackets: List[PriceBracket]
    fastest_selling: List[FastestSellingModel]
    trending_models: List[TrendingModel]
    scatter_data: List[ScatterPoint]
    stats: Dict[str, Any]

class PriceRange(BaseModel):
    min: float
    max: float
    avg: float

class FlippingRecommendation(BaseModel):
    model: str
    make: str
    buy_price_range: PriceRange
    sell_potential: float
    profit_margin: float
    avg_days_to_sell: float
    demand_level: str
    confidence_score: int
    sample_listings: int
    reasoning: str
    search_url: str

class MarketSummary(BaseModel):
    total_opportunities: int
    avg_roi: float
    fastest_flip: float

class FlippingAnalysisResponse(BaseModel):
    budget_min: float
    budget_max: float
    recommendations: List[FlippingRecommendation]
    market_summary: MarketSummary

class RefreshResponse(BaseModel):
    status: str
    message: str
    started_at: datetime

class LastUpdateResponse(BaseModel):
    last_update: Optional[datetime]
    total_listings: int
    new_today: int

# Helper functions
def load_flipper_data(timeframe_days: int, max_price: int, price_min: int = 0, year_min: int = 2000, year_max: int = 2024, transmission: str = "Any") -> List[CarListing]:
    """Load car data with filters"""
    try:
        engine, SessionLocal = create_engine_and_session()
        session = SessionLocal()
        
        cutoff_date = date.today() - timedelta(days=timeframe_days)
        
        query = session.query(CarListing).filter(
            CarListing.is_active == True,
            CarListing.price.isnot(None),
            CarListing.make.isnot(None),
            CarListing.posted_date.isnot(None),
            CarListing.posted_date >= cutoff_date,
            CarListing.price <= max_price,
            CarListing.price >= price_min
        )
        
        # Add year filters if specified
        if year_min and year_max:
            query = query.filter(
                CarListing.year.isnot(None),
                CarListing.year >= year_min,
                CarListing.year <= year_max
            )
        
        # Add transmission filter if not "Any"
        if transmission and transmission != "Any":
            transmission_map = {"Auto": "automatic", "Manual": "manual"}
            if transmission in transmission_map:
                query = query.filter(CarListing.transmission == transmission_map[transmission])
        
        listings = query.all()
        
        session.close()
        return listings
        
    except Exception as e:
        print(f"Error loading data: {e}")
        return []

def calculate_days_on_market(listings: List[CarListing], min_listings: int = 3) -> List[Dict]:
    """Calculate average days on market for each model"""
    model_data = defaultdict(list)
    
    for car in listings:
        if car.posted_date:
            days_on_market = (date.today() - car.posted_date).days
            key = f"{car.make} {car.model}".strip()
            model_data[key].append({
                'days': days_on_market,
                'price': car.price,
                'year': car.year,
                'views': car.views or 0
            })
    
    model_stats = []
    for model, data in model_data.items():
        if len(data) >= min_listings:
            avg_days = sum(item['days'] for item in data) / len(data)
            
            # Handle price calculation with error checking
            price_items = [item['price'] for item in data if item['price'] and item['price'] > 0]
            if price_items:
                avg_price = sum(price_items) / len(price_items)
            else:
                continue  # Skip models without valid prices
                
            avg_views = sum(item['views'] for item in data) / len(data)
            years = [item['year'] for item in data if item['year']]
            avg_year = int(sum(years) / len(years)) if years else 2020
            
            model_stats.append({
                'model': model,
                'avg_days_on_market': avg_days,
                'avg_price': avg_price,
                'avg_year': avg_year,
                'avg_views': avg_views
            })
    
    return sorted(model_stats, key=lambda x: x['avg_days_on_market'])

def analyze_price_ranges(listings: List[CarListing]) -> Dict[str, Dict]:
    """Analyze cars by price ranges"""
    price_ranges = {
        'Under 10K KM': (0, 10000),
        '10K - 20K KM': (10000, 20000),
        '20K - 30K KM': (20000, 30000),
        '30K - 40K KM': (30000, 40000),
        '40K - 50K KM': (40000, 50000),
        '50K - 60K KM': (50000, 60000),
        'Over 60K KM': (60000, float('inf'))
    }
    
    range_analysis = {}
    
    for range_name, (min_price, max_price) in price_ranges.items():
        range_cars = [car for car in listings if car.price and min_price <= car.price < max_price]
        
        if range_cars:
            model_data = defaultdict(list)
            
            for car in range_cars:
                if car.posted_date and car.make and car.model:
                    days = (date.today() - car.posted_date).days
                    model_key = f"{car.make} {car.model}".strip()
                    # Always create search URL for this car model on OLX.ba (more reliable than individual listings)
                    search_query = urllib.parse.quote(f"{car.make} {car.model}")
                    url = f"https://www.olx.ba/pretraga?trazilica={search_query}"
                    model_data[model_key].append({
                        'days': days,
                        'url': url,
                        'price': car.price,
                        'car': car
                    })
            
            model_averages = []
            for model, car_list in model_data.items():
                if len(car_list) >= 2:
                    avg_days = sum(item['days'] for item in car_list) / len(car_list)
                    # Get the car with days closest to average for sample
                    sample_car = min(car_list, key=lambda x: abs(x['days'] - avg_days))
                    model_averages.append((model, avg_days, sample_car['url'], sample_car['price']))
            
            model_averages.sort(key=lambda x: x[1])
            range_analysis[range_name] = {
                'models': model_averages[:5],  # Show top 5 instead of 3
                'total_cars': len(range_cars),
                'total_models': len(model_data)
            }
        else:
            range_analysis[range_name] = {
                'models': [],
                'total_cars': 0,
                'total_models': 0
            }
    
    return range_analysis

def create_scatter_data(listings: List[CarListing]) -> List[Dict]:
    """Create scatter plot data"""
    scatter_data = []
    
    for car in listings[:100]:  # Limit for performance
        if car.price and car.posted_date and car.mileage:
            days_on_market = (date.today() - car.posted_date).days
            
            if car.mileage < 50000:
                mileage_bracket = "<50k km"
            elif car.mileage < 100000:
                mileage_bracket = "50k-100k km"
            elif car.mileage < 150000:
                mileage_bracket = "100k-150k km"
            else:
                mileage_bracket = "150k+ km"
            
            scatter_data.append({
                'price': float(car.price),
                'days_on_market': float(days_on_market),
                'mileage_bracket': mileage_bracket,
                'model': f"{car.make} {car.model}".strip(),
                'views': int(car.views or 0)
            })
    
    return scatter_data

def analyze_flipping_opportunities(budget_min: float, budget_max: float) -> FlippingAnalysisResponse:
    """Analyze market for car flipping opportunities within budget"""
    try:
        # Get recent market data (30 days for faster analysis) - use max budget as upper limit
        listings = load_flipper_data(30, int(budget_max), 0, 2015, 2024)  # Focus on budget range
        
        if not listings:
            return FlippingAnalysisResponse(
                budget_min=budget_min,
                budget_max=budget_max,
                recommendations=[],
                market_summary=MarketSummary(
                    total_opportunities=0,
                    avg_roi=0.0,
                    fastest_flip=0.0
                )
            )
        
        # Calculate model performance metrics
        model_data = defaultdict(list)
        
        for car in listings:
            if car.posted_date and car.price and budget_min <= car.price <= budget_max:
                days_on_market = (date.today() - car.posted_date).days
                key = f"{car.make} {car.model}".strip()
                model_data[key].append({
                    'days': days_on_market,
                    'price': car.price,
                    'year': car.year or 2015,
                    'views': car.views or 0,
                    'make': car.make,
                    'model_name': car.model
                })
        
        # Analyze each model for flipping potential
        recommendations = []
        
        for model, data in model_data.items():
            if len(data) >= 2:  # Need at least 2 listings for analysis (faster)
                # Calculate key metrics
                prices = [item['price'] for item in data]
                days = [item['days'] for item in data]
                views = [item['views'] for item in data]
                
                avg_price = sum(prices) / len(prices)
                min_price = min(prices)
                max_price = max(prices)
                avg_days = sum(days) / len(days)
                avg_views = sum(views) / len(views)
                
                # Skip if average price is too close to max budget (no room for profit)
                # Also skip if it's too far below min budget (likely different market segment)
                if avg_price > budget_max * 0.85 or avg_price < budget_min * 0.5:
                    continue
                
                # Calculate profit potential (based on price appreciation and quick sale)
                # Estimate 15-25% markup potential for good flips
                sell_potential = avg_price * 1.20  # 20% average markup
                profit_margin = ((sell_potential - avg_price) / avg_price) * 100
                
                # Determine demand level based on days on market and views
                if avg_days <= 15 and avg_views >= 100:
                    demand_level = "High"
                elif avg_days <= 30 and avg_views >= 50:
                    demand_level = "Medium"
                else:
                    demand_level = "Low"
                
                # Calculate confidence score (0-100)
                confidence = 50
                if avg_days <= 20: confidence += 20  # Fast selling
                if avg_views >= 80: confidence += 15  # High interest
                if len(data) >= 10: confidence += 10  # Good sample size
                if profit_margin >= 15: confidence += 5  # Good profit potential
                
                confidence = min(confidence, 99)
                
                # Generate reasoning
                if avg_days <= 15:
                    speed_desc = "very fast seller"
                elif avg_days <= 25:
                    speed_desc = "fast seller"
                else:
                    speed_desc = "moderate seller"
                
                reasoning = f"This {speed_desc} averages {avg_days:.0f} days on market with {avg_views:.0f} views per listing."
                
                # Only recommend if it's a decent opportunity and fits within budget range
                if confidence >= 50 and avg_price <= budget_max * 0.95 and avg_price >= budget_min * 0.6 and profit_margin >= 8:
                    # Generate OLX.ba search URL with price filters
                    search_query = urllib.parse.quote(f"{data[0]['make']} {data[0]['model_name']}")
                    price_from = int(min_price)
                    price_to = int(min_price * 1.15)  # Same as max in buy_price_range
                    search_url = f"https://www.olx.ba/pretraga?trazilica={search_query}&price_from={price_from}&price_to={price_to}"
                    
                    recommendations.append(FlippingRecommendation(
                        model=data[0]['model_name'],
                        make=data[0]['make'],
                        buy_price_range=PriceRange(
                            min=float(min_price),
                            max=float(min_price * 1.15),  # Suggest slightly above min for better condition cars
                            avg=float(avg_price)
                        ),
                        sell_potential=float(sell_potential),
                        profit_margin=float(profit_margin),
                        avg_days_to_sell=round(float(avg_days), 1),
                        demand_level=demand_level,
                        confidence_score=int(confidence),
                        sample_listings=len(data),
                        reasoning=reasoning,
                        search_url=search_url
                    ))
        
        # Sort by confidence score and profit potential, then diversify by price range
        recommendations.sort(key=lambda x: (x.confidence_score, x.profit_margin), reverse=True)
        
        # Try to get a good mix across the budget range
        final_recommendations = []
        budget_third = (budget_max - budget_min) / 3
        
        # Categorize by price segments
        low_segment = [r for r in recommendations if r.buy_price_range.avg <= budget_min + budget_third]
        mid_segment = [r for r in recommendations if budget_min + budget_third < r.buy_price_range.avg <= budget_min + 2*budget_third]  
        high_segment = [r for r in recommendations if r.buy_price_range.avg > budget_min + 2*budget_third]
        
        # Take best from each segment for diversity
        final_recommendations.extend(low_segment[:2])
        final_recommendations.extend(mid_segment[:2]) 
        final_recommendations.extend(high_segment[:2])
        
        # Fill remaining slots if needed
        if len(final_recommendations) < 6:
            remaining_slots = 6 - len(final_recommendations)
            all_remaining = [r for r in recommendations if r not in final_recommendations]
            final_recommendations.extend(all_remaining[:remaining_slots])
        
        recommendations = final_recommendations[:6]
        
        # Calculate market summary
        if recommendations:
            avg_roi = sum(rec.profit_margin for rec in recommendations) / len(recommendations)
            fastest_flip = min(rec.avg_days_to_sell for rec in recommendations)
        else:
            avg_roi = 0.0
            fastest_flip = 0.0
        
        return FlippingAnalysisResponse(
            budget_min=budget_min,
            budget_max=budget_max,
            recommendations=recommendations,
            market_summary=MarketSummary(
                total_opportunities=len(recommendations),
                avg_roi=avg_roi,
                fastest_flip=fastest_flip
            )
        )
        
    except Exception as e:
        print(f"Error in flipping analysis: {e}")
        return FlippingAnalysisResponse(
            budget_min=budget_min,
            budget_max=budget_max,
            recommendations=[],
            market_summary=MarketSummary(
                total_opportunities=0,
                avg_roi=0.0,
                fastest_flip=0.0
            )
        )

# API Endpoints
@app.get("/")
async def root():
    return {"message": "Car Flipper Dashboard API", "version": "1.0.0"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now()}

@app.get("/test-urls")
async def test_urls():
    """Test URL generation"""
    try:
        listings = load_flipper_data(30, 20000)
        sample_urls = []
        for car in listings[:5]:
            search_query = urllib.parse.quote(f"{car.make} {car.model}")
            url = car.listing_url if car.listing_url else f"https://www.olx.ba/pretraga?trazilica={search_query}"
            sample_urls.append({
                'make': car.make,
                'model': car.model,
                'listing_id': car.listing_id,
                'original_url': car.listing_url,
                'generated_url': url
            })
        return sample_urls
    except Exception as e:
        return {"error": str(e)}

@app.get("/dashboard", response_model=DashboardData)
async def get_dashboard_data(
    timeframe_days: int = Query(60, ge=7, le=365, description="Number of days to analyze"),
    max_price: int = Query(25000, ge=1000, le=200000, description="Maximum price filter"),
    price_min: int = Query(0, ge=0, le=100000, description="Minimum price filter"),
    min_listings: int = Query(5, ge=2, le=20, description="Minimum listings per model"),
    year_min: int = Query(2000, ge=1990, le=2024, description="Minimum year filter"),
    year_max: int = Query(2024, ge=1990, le=2024, description="Maximum year filter"),
    transmission: str = Query("Any", description="Transmission type: Any, Auto, Manual")
):
    """Get complete dashboard data"""
    try:
        # Validate parameters
        if price_min >= max_price:
            raise HTTPException(status_code=422, detail="Minimum price must be less than maximum price")
        
        if year_min > year_max:
            raise HTTPException(status_code=422, detail="Minimum year must be less than or equal to maximum year")
        
        # Load data
        listings = load_flipper_data(timeframe_days, max_price, price_min, year_min, year_max, transmission)
        
        if not listings:
            # Return empty dashboard instead of 404
            return DashboardData(
                opportunities=[],
                price_brackets=[],
                fastest_selling=[],
                trending_models=[],
                scatter_data=[],
                stats={
                    "total_listings": 0,
                    "timeframe_days": timeframe_days,
                    "max_price": max_price,
                    "models_analyzed": 0,
                    "last_updated": datetime.now().isoformat()
                }
            )
        
        # Calculate analyses with error handling
        try:
            days_analysis = calculate_days_on_market(listings, min_listings)
        except Exception as e:
            print(f"Error in days analysis: {e}")
            days_analysis = []
            
        try:
            price_range_analysis = analyze_price_ranges(listings)
        except Exception as e:
            print(f"Error in price range analysis: {e}")
            price_range_analysis = {}
            
        try:
            scatter_data = create_scatter_data(listings)
        except Exception as e:
            print(f"Error in scatter data: {e}")
            scatter_data = []
        
        # Build opportunities
        opportunities = []
        
        # Hot Flip
        if days_analysis:
            hot_flip = days_analysis[0]
            days_faster = max(0, 30 - hot_flip['avg_days_on_market'])
            opportunities.append(OpportunityCard(
                type="hot_flip",
                title=f"Hot Flip: {hot_flip['model']}",
                subtitle=f"High demand this period. Sells {days_faster:.0f} days faster than average.",
                model=hot_flip['model'],
                days=hot_flip['avg_days_on_market'],
                color="green"
            ))
        
        # Overpriced
        slow_models = [m for m in days_analysis if m['avg_days_on_market'] > 35]
        if slow_models:
            overpriced = slow_models[0]
            opportunities.append(OpportunityCard(
                type="overpriced",
                title=f"Overpriced: {overpriced['model']}",
                subtitle=f"Avg. {overpriced['avg_days_on_market']:.0f} days on market. Advise cautious pricing.",
                model=overpriced['model'],
                days=overpriced['avg_days_on_market'],
                color="orange"
            ))
        else:
            opportunities.append(OpportunityCard(
                type="overpriced",
                title="Market Analysis",
                subtitle="No major overpricing detected in current filters.",
                model=None,
                days=None,
                color="orange"
            ))
        
        # Rising Demand (simplified)
        opportunities.append(OpportunityCard(
            type="rising_demand",
            title="Rising Demand: Monitoring",
            subtitle="Analyzing trends for emerging opportunities.",
            model=None,
            days=None,
            color="blue"
        ))
        
        # Build price brackets
        price_brackets = []
        for bracket_name, bracket_data in price_range_analysis.items():
            bracket_models = [
                PriceBracketModel(
                    rank=i+1, 
                    model=model, 
                    days=days,
                    sample_url=url,
                    sample_price=price
                )
                for i, (model, days, url, price) in enumerate(bracket_data['models'])
            ]
            price_brackets.append(PriceBracket(
                bracket_name=bracket_name,
                models=bracket_models,
                total_cars=bracket_data['total_cars'],
                total_models=bracket_data['total_models']
            ))
        
        # Build fastest selling models
        fastest_selling = []
        for model_data in days_analysis[:10]:
            days = model_data['avg_days_on_market']
            if days <= 15:
                demand_level = "High"
            elif days <= 30:
                demand_level = "Medium"
            else:
                demand_level = "Low"
            
            fastest_selling.append(FastestSellingModel(
                model=model_data['model'],
                year=model_data['avg_year'],
                avg_days_on_market=days,
                avg_price=model_data['avg_price'],
                demand_level=demand_level
            ))
        
        # Build trending models from real data
        trending_models = []
        
        # Get models with high views (top 3)
        high_view_models = sorted([m for m in days_analysis if m.get('avg_views', 0) > 50], 
                                 key=lambda x: x.get('avg_views', 0), reverse=True)[:2]
        
        for model_data in high_view_models:
            trending_models.append(TrendingModel(
                model=model_data['model'],
                trend_text=f"High engagement: {model_data.get('avg_views', 0):.0f} avg views",
                trend_type="views_up"
            ))
        
        # Get models selling quickly (under 10 days)
        fast_selling = [m for m in days_analysis if m['avg_days_on_market'] < 10][:1]
        for model_data in fast_selling:
            trending_models.append(TrendingModel(
                model=model_data['model'],
                trend_text=f"Fast seller: {model_data['avg_days_on_market']:.1f} days avg",
                trend_type="days_down"
            ))
        
        # Fill with general trending if needed
        while len(trending_models) < 3 and len(days_analysis) > len(trending_models):
            idx = len(trending_models)
            if idx < len(days_analysis):
                model_data = days_analysis[idx]
                trending_models.append(TrendingModel(
                    model=model_data['model'],
                    trend_text=f"Market performer: {model_data['avg_days_on_market']:.1f} days avg",
                    trend_type="general"
                ))
        
        # Build scatter data
        scatter_points = [ScatterPoint(**point) for point in scatter_data]
        
        # Build stats
        stats = {
            "total_listings": len(listings),
            "timeframe_days": timeframe_days,
            "max_price": max_price,
            "models_analyzed": len(days_analysis),
            "last_updated": datetime.now().isoformat()
        }
        
        return DashboardData(
            opportunities=opportunities,
            price_brackets=price_brackets,
            fastest_selling=fastest_selling,
            trending_models=trending_models,
            scatter_data=scatter_points,
            stats=stats
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing dashboard data: {str(e)}")

@app.get("/listings", response_model=List[CarListingResponse])
async def get_listings(
    limit: int = Query(100, ge=1, le=1000),
    timeframe_days: int = Query(60, ge=7, le=365),
    max_price: int = Query(25000, ge=1000, le=200000),
    price_min: int = Query(0, ge=0, le=100000),
    year_min: int = Query(2000, ge=1990, le=2024),
    year_max: int = Query(2024, ge=1990, le=2024),
    transmission: str = Query("Any")
):
    """Get car listings with filters"""
    try:
        listings = load_flipper_data(timeframe_days, max_price, price_min, year_min, year_max, transmission)
        
        return [
            CarListingResponse(
                listing_id=car.listing_id,
                make=car.make,
                model=car.model,
                year=car.year,
                price=car.price,
                mileage=car.mileage,
                views=car.views,
                posted_date=car.posted_date,
                location=car.location,
                fuel_type=car.fuel_type,
                transmission=car.transmission
            )
            for car in listings[:limit]
        ]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching listings: {str(e)}")

@app.get("/analyze-flipping", response_model=FlippingAnalysisResponse)
async def analyze_car_flipping(
    budget_min: float = Query(..., ge=1000, le=100000, description="Minimum budget for car purchase"),
    budget_max: float = Query(..., ge=1000, le=100000, description="Maximum budget for car purchase")
):
    """Analyze car flipping opportunities for given budget range"""
    try:
        if budget_min >= budget_max:
            raise HTTPException(status_code=400, detail="Budget minimum must be less than budget maximum")
        
        analysis = analyze_flipping_opportunities(budget_min, budget_max)
        return analysis
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error analyzing flipping opportunities: {str(e)}")

@app.post("/refresh-data", response_model=RefreshResponse)
async def refresh_data():
    """Trigger manual data refresh by running the scraper"""
    try:
        # Get project root directory (parent of api directory)
        project_root = Path(__file__).parent.parent
        run_scraper_path = project_root / "run_scraper.py"
        venv_activate = project_root / "venv" / "bin" / "activate"
        
        if not run_scraper_path.exists():
            raise HTTPException(status_code=500, detail="Scraper script not found")
        
        # Start scraper in background with timeout
        started_at = datetime.now()
        
        # Run scraper with a timeout of 30 seconds for quick refresh
        asyncio.create_task(run_scraper_background(project_root))
        
        return RefreshResponse(
            status="started",
            message="Data refresh started in background. This may take a few minutes.",
            started_at=started_at
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error starting data refresh: {str(e)}")

@app.get("/last-update", response_model=LastUpdateResponse)
async def get_last_update():
    """Get information about the last data update"""
    try:
        engine, SessionLocal = create_engine_and_session()
        session = SessionLocal()
        
        # Get most recent scraping timestamp
        latest_listing = session.query(CarListing).filter(
            CarListing.is_active == True
        ).order_by(desc(CarListing.scraped_at)).first()
        
        # Get total count and today's count
        total_count = session.query(CarListing).filter(CarListing.is_active == True).count()
        
        today_count = session.query(CarListing).filter(
            CarListing.is_active == True,
            func.date(CarListing.scraped_at) == func.current_date()
        ).count()
        
        session.close()
        
        last_update = latest_listing.scraped_at if latest_listing else None
        
        return LastUpdateResponse(
            last_update=last_update,
            total_listings=total_count,
            new_today=today_count
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting last update info: {str(e)}")

async def run_scraper_background(project_root: Path):
    """Run scraper in background with timeout"""
    try:
        # Create command to run scraper with timeout
        cmd = f"cd '{project_root}' && source venv/bin/activate && timeout 30 python run_scraper.py"
        
        # Run in background
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # Wait for completion with timeout
        try:
            await asyncio.wait_for(process.wait(), timeout=35)
        except asyncio.TimeoutError:
            process.terminate()
            await process.wait()
        
    except Exception as e:
        print(f"Background scraper error: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)