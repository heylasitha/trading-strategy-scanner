#!/bin/bash

cd "$(dirname "$0")"

echo "======================================"
echo "  Trading Strategy Scanner"
echo "======================================"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install / upgrade dependencies
echo "Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo ""
echo "Starting scanner..."
echo "Alerts will be sent to your Telegram."
echo "Press Ctrl+C to stop."
echo "======================================"
echo ""

python3 scanner.py
