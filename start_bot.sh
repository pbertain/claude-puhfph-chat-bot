#!/bin/bash
# Startup script for claude-puhfph-chat-bot
# Activates virtual environment and runs the iMessage listener bot

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# Activate virtual environment
# If you want to use a different venv, change this path:
VENV_PATH="$SCRIPT_DIR/venv/bin/activate"

if [ ! -f "$VENV_PATH" ]; then
    echo "Error: Virtual environment not found at $VENV_PATH" >&2
    echo "Please run setup.sh first to create the virtual environment." >&2
    exit 1
fi

source "$VENV_PATH"

# Run the bot
exec python3 imessage-listener.py
