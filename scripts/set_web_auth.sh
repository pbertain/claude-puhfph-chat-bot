#!/bin/bash
# Configure Basic Auth for the web troubleshooting interface (launchctl).

set -e

if [ $# -lt 2 ]; then
    echo "Usage: $0 <username> <password> [plist_path]" >&2
    exit 1
fi

USER_NAME="$1"
USER_PASS="$2"
PLIST_PATH="${3:-$HOME/Library/LaunchAgents/com.claudepuhfph.chatbot.web.plist}"

if [ ! -f "$PLIST_PATH" ]; then
    echo "Error: launchctl plist not found at $PLIST_PATH" >&2
    echo "Run ./install_web_launchctl.sh first to install it." >&2
    exit 1
fi

PLISTBUDDY="/usr/libexec/PlistBuddy"

if [ ! -x "$PLISTBUDDY" ]; then
    echo "Error: PlistBuddy not found at $PLISTBUDDY" >&2
    exit 1
fi

# Ensure EnvironmentVariables exists
if ! "$PLISTBUDDY" -c "Print :EnvironmentVariables" "$PLIST_PATH" >/dev/null 2>&1; then
    "$PLISTBUDDY" -c "Add :EnvironmentVariables dict" "$PLIST_PATH"
fi

set_or_add_key() {
    local key="$1"
    local value="$2"
    if "$PLISTBUDDY" -c "Print :EnvironmentVariables:$key" "$PLIST_PATH" >/dev/null 2>&1; then
        "$PLISTBUDDY" -c "Set :EnvironmentVariables:$key $value" "$PLIST_PATH"
    else
        "$PLISTBUDDY" -c "Add :EnvironmentVariables:$key string $value" "$PLIST_PATH"
    fi
}

set_or_add_key "TROUBLESHOOTING_USER" "$USER_NAME"
set_or_add_key "TROUBLESHOOTING_PASS" "$USER_PASS"

echo "Updated launchctl env vars in $PLIST_PATH"
echo "Reloading service..."

launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load "$PLIST_PATH"

echo "Done. The web UI will now prompt for a username/password."
