#!/bin/bash
# Startup script for claude-puhfph-chat-bot
# Activates virtual environment and runs the iMessage listener bot

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# Activate virtual environment
# First try shared venv, then fall back to local venv
SHARED_VENV="/Users/claudep/tools/venv/bin/activate"
LOCAL_VENV="$SCRIPT_DIR/venv/bin/activate"

if [ -f "$SHARED_VENV" ]; then
    VENV_PATH="$SHARED_VENV"
elif [ -f "$LOCAL_VENV" ]; then
    VENV_PATH="$LOCAL_VENV"
else
    echo "Error: Virtual environment not found at $SHARED_VENV or $LOCAL_VENV" >&2
    echo "Please create a virtual environment or update the VENV_PATH in this script." >&2
    exit 1
fi

source "$VENV_PATH"

# Run the bot
exec python3 imessage-listener.py
