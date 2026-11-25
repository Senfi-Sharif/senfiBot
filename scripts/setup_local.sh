#!/bin/bash

# Council Bot - Local Setup Script
# This script sets up the development environment

echo "🔧 Setting up Council Bot development environment..."

# Get the project root directory (parent of scripts directory)
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "📁 Project directory: $PROJECT_ROOT"

# Change to project root directory
cd "$PROJECT_ROOT"

# Create virtual environment
echo "📦 Creating virtual environment..."
python3 -m venv venv

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "📥 Installing dependencies..."
pip install python-telegram-bot python-dotenv

# Create requirements.txt if it doesn't exist
if [ ! -f "requirements.txt" ]; then
    echo "📝 Creating requirements.txt..."
    pip freeze > requirements.txt
fi

# Check if .env file exists, if not copy from example
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        echo "📋 Copying .env.example to .env..."
        cp .env.example .env
        echo "⚠️  Please edit .env file with your actual configuration!"
    else
        echo "❌ Error: .env.example not found!"
        exit 1
    fi
fi

# Initialize database
echo "🗄️ Initializing database..."
python3 -c "
from database import Database
Database()
print('Database initialized successfully!')
"

echo "✅ Setup completed!"
echo ""
echo "📋 Next steps:"
echo "1. Edit .env file with your configuration"
echo "2. Run ./run_bot.sh to start the bot"
echo "3. Run ./test_local.sh to test the bot"
echo "4. Run ./admin_panel_local.sh to access admin panel" 
