#!/bin/bash

# OLX Scraper - Start All Services
# This script starts the API backend and frontend dashboard

echo "🚀 Starting OLX Car Market Analysis System..."

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found. Please run setup first:"
    echo "   python -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Function to check if port is in use
check_port() {
    lsof -ti:$1 >/dev/null 2>&1
    return $?
}

# Kill existing processes on our ports
echo "🧹 Cleaning up existing processes..."
if check_port 8000; then
    echo "   Stopping process on port 8000"
    lsof -ti:8000 | xargs kill -9 2>/dev/null || true
fi

if check_port 3000; then
    echo "   Stopping process on port 3000"  
    lsof -ti:3000 | xargs kill -9 2>/dev/null || true
fi

if check_port 8080; then
    echo "   Stopping process on port 8080"
    lsof -ti:8080 | xargs kill -9 2>/dev/null || true
fi

sleep 2

# Activate virtual environment
echo "🔧 Activating Python virtual environment..."
source venv/bin/activate

# Start API Backend
echo "🖥️  Starting API Backend (Port 8000)..."
cd api
uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
API_PID=$!
cd ..

# Wait for API to start
echo "⏳ Waiting for API to start..."
sleep 5

# Check if API is running
if ! check_port 8000; then
    echo "❌ API failed to start on port 8000"
    kill $API_PID 2>/dev/null || true
    exit 1
fi

echo "✅ API Backend started successfully at http://localhost:8000"

# Start Frontend (if Node.js is available)
if command -v npm >/dev/null 2>&1; then
    echo "🎨 Starting React Frontend (Port 8080)..."
    cd frontend
    
    # Install dependencies if node_modules doesn't exist
    if [ ! -d "node_modules" ]; then
        echo "📦 Installing frontend dependencies..."
        npm install
    fi
    
    # Start frontend in development mode
    npm run dev -- --port 8080 --host 0.0.0.0 &
    FRONTEND_PID=$!
    cd ..
    
    echo "⏳ Waiting for frontend to start..."
    sleep 8
    
    if check_port 8080; then
        echo "✅ Frontend started successfully at http://localhost:8080"
    else
        echo "⚠️  Frontend may still be starting... Check http://localhost:8080 in a few moments"
    fi
else
    echo "⚠️  Node.js not found. Frontend not started."
    echo "   Install Node.js to run the React dashboard: https://nodejs.org/"
fi

echo ""
echo "🎉 OLX Car Market Analysis System is running!"
echo ""
echo "📊 Services:"
echo "   • API Backend:    http://localhost:8000"
echo "   • API Docs:       http://localhost:8000/docs"
if command -v npm >/dev/null 2>&1; then
echo "   • Dashboard:      http://localhost:8080"
fi
echo ""
echo "🛑 To stop all services, press Ctrl+C or run:"
echo "   ./stop_all.sh"
echo ""

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "🛑 Stopping all services..."
    kill $API_PID 2>/dev/null || true
    if [ ! -z "$FRONTEND_PID" ]; then
        kill $FRONTEND_PID 2>/dev/null || true
    fi
    
    # Force kill any remaining processes
    lsof -ti:8000 | xargs kill -9 2>/dev/null || true
    lsof -ti:8080 | xargs kill -9 2>/dev/null || true
    
    echo "✅ All services stopped"
    exit 0
}

# Trap Ctrl+C and cleanup
trap cleanup SIGINT

# Keep script running
wait