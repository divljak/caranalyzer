# 🔥 Car Flipper Dashboard - React + Chakra UI

A modern, professional car market analysis dashboard built with React, Chakra UI, and FastAPI.

## 🏗️ Architecture

- **Frontend**: React 18 + Chakra UI + Recharts
- **Backend**: FastAPI + SQLAlchemy + PostgreSQL  
- **Styling**: Chakra UI design system
- **Charts**: Recharts for scatter plot visualization

## 🚀 Quick Start

### Prerequisites

- Node.js 16+ and npm
- Python 3.8+
- PostgreSQL database (your existing OLX.ba data)

### 1. Setup Backend (FastAPI)

```bash
# Navigate to project root
cd "/Users/ognjendivljak/Desktop/Projects/olx scraper"

# Install Python dependencies
pip install -r api/requirements.txt

# Start the FastAPI server
cd api
python main.py
```

The API will be available at: `http://localhost:8000`

### 2. Setup Frontend (React)

```bash
# Navigate to frontend directory
cd "/Users/ognjendivljak/Desktop/Projects/olx scraper/frontend"

# Install Node.js dependencies
npm install

# Start the React development server
npm start
```

The dashboard will be available at: `http://localhost:3000`

## 📁 Project Structure

```
olx scraper/
├── api/                          # FastAPI Backend
│   ├── main.py                   # FastAPI app with all endpoints
│   └── requirements.txt          # Python dependencies
├── frontend/                     # React Frontend
│   ├── public/
│   │   └── index.html           # HTML template
│   ├── src/
│   │   ├── components/          # React components
│   │   │   ├── FilterSidebar.js # Left sidebar with filters
│   │   │   ├── TopOpportunities.js # Opportunity cards
│   │   │   ├── PriceBrackets.js # Price bracket analysis
│   │   │   ├── MarketQuadrant.js # Scatter plot + trending
│   │   │   └── FastestSellingTable.js # Data table
│   │   ├── App.js               # Main app component
│   │   ├── api.js               # API client
│   │   ├── theme.js             # Chakra UI theme
│   │   └── index.js             # React entry point
│   └── package.json             # Node.js dependencies
├── database/                     # Your existing database layer
└── dashboard/                    # Your old Streamlit dashboards
```

## 🎨 Features

### ✅ **Modern UI with Chakra UI**
- Professional design system
- Consistent spacing, colors, and typography
- Responsive layout for all screen sizes
- Smooth animations and hover effects

### ✅ **Interactive Filter Sidebar**
- Date range selection (30/60/90 days)
- Price range inputs with steppers
- Year range filtering
- Transmission type selection
- Real-time filter application

### ✅ **Top Opportunities Cards**
- Hot Flip opportunities (green)
- Overpriced warnings (orange)  
- Rising demand alerts (blue)
- Interactive hover effects

### ✅ **Market Insights by Price Bracket**
- Three price ranges with top 3 models each
- Clean card-based layout
- Days on market for each model

### ✅ **Market Quadrant Analysis**
- Interactive scatter plot with Recharts
- Price vs Days on Market visualization
- Color-coded by mileage brackets
- Reference lines for averages
- Detailed tooltips on hover

### ✅ **Trending Models Sidebar**
- Models with increasing views
- Models with decreasing days on market
- Color-coded trend indicators

### ✅ **Fastest Selling Models Table**
- Professional data table
- Sortable columns
- Demand level badges (High/Medium/Low)
- Hover effects for better UX

## 🔌 API Endpoints

The FastAPI backend provides these endpoints:

- `GET /` - API information
- `GET /health` - Health check
- `GET /dashboard` - Complete dashboard data
  - Query params: `timeframe_days`, `max_price`, `min_listings`
- `GET /listings` - Car listings with filters

## 🎯 Data Flow

1. **React Frontend** makes API calls to FastAPI backend
2. **FastAPI Backend** queries your existing PostgreSQL database
3. **Database Layer** uses your existing models and db_manager
4. **Real-time filtering** - filters applied instantly
5. **Cached responses** - FastAPI caches data for performance

## 🔧 Customization

### Colors & Theme
Edit `frontend/src/theme.js` to customize:
- Brand colors
- Component styles  
- Fonts and typography

### API Configuration
Edit `frontend/src/api.js` to change:
- API base URL
- Request timeout
- Error handling

### Components
All React components in `frontend/src/components/` are modular and can be:
- Styled independently
- Reordered or removed
- Enhanced with additional features

## 🚀 Production Deployment

### Frontend (React)
```bash
cd frontend
npm run build
# Deploy the 'build' folder to any static hosting
```

### Backend (FastAPI)
```bash
# Use a production ASGI server like Gunicorn
pip install gunicorn
gunicorn api.main:app -w 4 -k uvicorn.workers.UvicornWorker
```

## 🎉 Benefits Over Streamlit

1. **⚡ Performance**: Much faster loading and interactions
2. **🎨 Design**: Professional, modern UI that looks native
3. **📱 Responsive**: Works perfectly on all device sizes
4. **🔧 Customizable**: Full control over every aspect of the UI
5. **🚀 Scalable**: Can easily add new features and components
6. **💡 Interactive**: Smooth animations and hover effects
7. **🔌 API-First**: Clean separation between frontend and backend

## 🛠️ Development

To add new features:

1. **Add API endpoint** in `api/main.py`
2. **Create React component** in `frontend/src/components/`
3. **Update API client** in `frontend/src/api.js`
4. **Import component** in `frontend/src/App.js`

The dashboard is fully modular and easy to extend!

---

**Your Car Flipper Dashboard is now a modern, professional React application! 🎉**