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

# Check if shared venv exists and is readable
if [ -r "$SHARED_VENV" ]; then
    VENV_PATH="$SHARED_VENV"
elif [ -f "$LOCAL_VENV" ]; then
    VENV_PATH="$LOCAL_VENV"
else
    echo "Error: Virtual environment not found!" >&2
    echo "  Checked: $SHARED_VENV" >&2
    if [ ! -e "$SHARED_VENV" ]; then
        echo "    (File does not exist)" >&2
    elif [ ! -r "$SHARED_VENV" ]; then
        echo "    (File exists but is not readable)" >&2
    fi
    echo "  Checked: $LOCAL_VENV" >&2
    if [ ! -e "$LOCAL_VENV" ]; then
        echo "    (File does not exist)" >&2
    elif [ ! -r "$LOCAL_VENV" ]; then
        echo "    (File exists but is not readable)" >&2
    fi
    echo "Please create a virtual environment or update the VENV_PATH in this script." >&2
    exit 1
fi

source "$VENV_PATH"

# Run the bot
exec python3 imessage-listener.py
