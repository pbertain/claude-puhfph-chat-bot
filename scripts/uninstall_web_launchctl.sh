#!/bin/bash
# Uninstall launchctl service for web troubleshooting interface

set -e

PLIST_NAME="com.claudepuhfph.chatbot.web.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "Uninstalling web troubleshooting launchctl service..."

# Unload the service if it's running
if launchctl list | grep -q "claudepuhfph.chatbot.web"; then
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
fi

# Remove the plist file
if [ -f "$PLIST_DEST" ]; then
    rm "$PLIST_DEST"
    echo "Service uninstalled!"
else
    echo "Service was not installed."
fi
