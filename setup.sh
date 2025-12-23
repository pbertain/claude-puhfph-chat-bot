#!/bin/bash
# Setup script for claude-puhfph-chat-bot

set -e

echo "Setting up claude-puhfph-chat-bot..."

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
else
    echo "Virtual environment already exists."
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install --upgrade pip
pip install -e .

echo ""
echo "Setup complete! To run the bot:"
echo "  1. Activate the virtual environment: source venv/bin/activate"
echo "  2. Run the bot: python3 imessage-listener.py"

