#!/bin/bash
# Install launchctl service for claude-puhfph-chat-bot

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_NAME="com.claudepuhfph.chatbot.plist"
PLIST_SOURCE="$SCRIPT_DIR/$PLIST_NAME"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "Installing launchctl service..."

# Update the plist file with the correct user path
# Replace the hardcoded path with the actual script directory
sed "s|/Users/paulb/Documents/version-control/git/claude-puhfph-chat-bot|$SCRIPT_DIR|g" "$PLIST_SOURCE" > "$PLIST_DEST"

# Load the service
launchctl load "$PLIST_DEST"

echo "Service installed and started!"
echo ""
echo "To check status: launchctl list | grep claudepuhfph"
echo "To stop: launchctl unload $PLIST_DEST"
echo "To start: launchctl load $PLIST_DEST"
echo "To view logs: tail -f $SCRIPT_DIR/bot.log"
