#!/bin/bash

echo "🛑 Stopping OLX Car Market Analysis System..."

# Function to check if port is in use and kill process
stop_port() {
    if lsof -ti:$1 >/dev/null 2>&1; then
        echo "   Stopping process on port $1"
        lsof -ti:$1 | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
}

# Stop all services
stop_port 8000  # API Backend
stop_port 8080  # Frontend
stop_port 3000  # Frontend (alternative port)

echo "✅ All services stopped"