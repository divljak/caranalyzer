#!/usr/bin/env python3
"""
Smart Car Buying Alerts - Generate weekly recommendations
"""
import sys
sys.path.insert(0, '.')

from database.db_manager import db_manager
from datetime import datetime, date, timedelta
from collections import Counter
import json

def generate_weekly_alerts(budget=15000):
    """Generate smart buying alerts for the week"""
    print(f"🧠 SMART CAR BUYING ALERTS - Week of {date.today()}")
    print("=" * 60)
    print(f"💰 Budget: {budget:,} KM")
    print()
    
    # Get recent listings
    try:
        recent_listings = db_manager.get_todays_listings() + db_manager.get_hot_cars(7, 200)
        budget_cars = [car for car in recent_listings if car.price and car.price <= budget]
        
        if not budget_cars:
            print(f"❌ No cars found within {budget:,} KM budget")
            return
            
        print(f"📊 Analyzing {len(budget_cars)} cars in your budget...")
        print()
        
        # 1. TOP 5 CARS YOU SHOULD BUY THIS WEEK
        print("🔥 TOP 5 CARS YOU SHOULD BUY THIS WEEK")
        print("-" * 40)
        
        # Score cars based on multiple factors
        scored_cars = []
        for car in budget_cars:
            score = 0
            reasons = []
            
            # Recent posting bonus
            if car.posted_date:
                days_old = (date.today() - car.posted_date).days
                if days_old <= 3:
                    score += 20
                    reasons.append("Fresh listing")
            
            # Popular make bonus
            if car.make in ['Volkswagen', 'BMW', 'Audi', 'Mercedes-Benz', 'Skoda']:
                score += 15
                reasons.append("Popular brand")
            
            # Good year bonus (2015+)
            if car.year and car.year >= 2015:
                score += 10
                reasons.append("Recent model")
            
            # Reasonable mileage bonus
            if car.mileage and car.mileage < 150000:
                score += 10
                reasons.append("Low mileage")
            
            # Good views to price ratio
            if car.views and car.price:
                views_per_1000km = car.views / (car.price / 1000)
                if views_per_1000km > 2:
                    score += 5
                    reasons.append("High interest")
            
            scored_cars.append({
                'car': car,
                'score': score,
                'reasons': reasons
            })
        
        # Sort by score and show top 5
        scored_cars.sort(key=lambda x: x['score'], reverse=True)
        
        for i, item in enumerate(scored_cars[:5], 1):
            car = item['car']
            reasons_text = ", ".join(item['reasons']) if item['reasons'] else "Good value"
            
            print(f"{i}. {car.make} {car.model} {car.year or 'N/A'}")
            print(f"   💰 Price: {car.price:,} KM")
            print(f"   🛣️  Mileage: {car.mileage:,} km" if car.mileage else "   🛣️  Mileage: N/A")
            print(f"   👀 Views: {car.views}")
            print(f"   ⭐ Score: {item['score']}/60 - {reasons_text}")
            print(f"   🔗 URL: {car.listing_url}")
            print()
        
        # 2. MARKET INSIGHTS
        print("📈 WEEKLY MARKET INSIGHTS")
        print("-" * 30)
        
        # Most popular makes
        makes_counter = Counter([car.make for car in budget_cars if car.make])
        print("🏆 Hottest Brands This Week:")
        for make, count in makes_counter.most_common(3):
            print(f"   • {make}: {count} listings")
        print()
        
        # Price ranges
        prices = [car.price for car in budget_cars if car.price]
        if prices:
            print("💰 Price Analysis:")
            print(f"   • Average: {sum(prices)/len(prices):,.0f} KM")
            print(f"   • Cheapest: {min(prices):,} KM")
            print(f"   • Most expensive: {max(prices):,} KM")
            print()
        
        # 3. QUICK ACTION ITEMS
        print("⚡ QUICK ACTION ITEMS FOR THIS WEEK")
        print("-" * 35)
        print("1. 🔍 Set up alerts for your top 3 preferred models")
        print("2. 📞 Contact sellers of top-scored cars immediately")
        print("3. 🚗 Plan to view at least 2-3 cars this weekend")
        print("4. 💳 Get financing pre-approved for faster purchasing")
        print("5. 🔄 Check back tomorrow for new listings")
        print()
        
        # 4. DEALS TO WATCH
        cheap_luxury = [car for car in budget_cars 
                       if car.make in ['BMW', 'Mercedes-Benz', 'Audi'] 
                       and car.price and car.price < 20000]
        
        if cheap_luxury:
            print("💎 LUXURY DEALS TO INVESTIGATE")
            print("-" * 30)
            for car in cheap_luxury[:3]:
                print(f"• {car.make} {car.model} {car.year or 'N/A'} - {car.price:,} KM")
                print(f"  ⚠️  Verify condition - could be great deal or hidden issues")
            print()
        
        print("✅ Alert generation complete!")
        print(f"📧 Next alert: {(date.today() + timedelta(days=7)).strftime('%B %d, %Y')}")
        
    except Exception as e:
        print(f"❌ Error generating alerts: {e}")

if __name__ == "__main__":
    # You can change the budget here or make it a command line argument
    budget = 15000  # Default budget
    
    if len(sys.argv) > 1:
        try:
            budget = int(sys.argv[1])
        except:
            print("Usage: python smart_alerts.py [budget_in_KM]")
            sys.exit(1)
    
    generate_weekly_alerts(budget)