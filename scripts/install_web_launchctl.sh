#!/bin/bash
# Install launchctl service for web troubleshooting interface

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PLIST_NAME="com.claudepuhfph.chatbot.web.plist"
PLIST_SOURCE="$REPO_DIR/launchd/$PLIST_NAME"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "Installing web troubleshooting launchctl service..."

# Create LaunchAgents directory if it doesn't exist
mkdir -p "$HOME/Library/LaunchAgents"

# Create log files if they don't exist (launchctl requires them to exist)
touch "$REPO_DIR/web_troubleshooting.log"
touch "$REPO_DIR/web_troubleshooting_error.log"

# Update the plist file with the correct user path
# Replace the hardcoded path with the actual repo directory
sed "s|/Users/claude/tools/claude-puhfph-chat-bot|$REPO_DIR|g" "$PLIST_SOURCE" > "$PLIST_DEST"

# Load the service
launchctl load "$PLIST_DEST"

echo "Web troubleshooting service installed and started!"
echo ""
echo "To check status: launchctl list | grep claudepuhfph"
echo "To stop: launchctl unload $PLIST_DEST"
echo "To start: launchctl load $PLIST_DEST"
echo "To view logs: tail -f $REPO_DIR/web_troubleshooting.log"
echo ""
echo "Access the web interface at: http://localhost:55042"
