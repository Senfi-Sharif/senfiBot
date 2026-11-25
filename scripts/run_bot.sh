#!/bin/bash

# Council Bot - Local Run Script
# This script runs the bot locally from the project folder

echo "🤖 Starting Council Bot locally..."

# Get the project root directory (parent of scripts directory)
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "📁 Project directory: $PROJECT_ROOT"

# Change to project root directory
cd "$PROJECT_ROOT"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "📥 Installing dependencies..."
pip install -r requirements.txt

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo "❌ Error: .env file not found in $PROJECT_ROOT"
    echo "Please copy .env.example to .env and configure it."
    exit 1
fi

# Initialize database
echo "🗄️ Initializing database..."
python3 -c "
from database import Database
Database()
print('Database initialized successfully!')
"

# Run the bot
echo "🚀 Starting bot..."
echo "Press Ctrl+C to stop the bot"
python3 enhanced_bot.py 
