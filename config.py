#!/usr/bin/env python3
"""
Configuration constants and paths.
"""
import pathlib

# File paths
STATE_FILE = pathlib.Path.home() / ".imessage_autoreply_last_rowid"
CHAT_DB = pathlib.Path.home() / "Library/Messages/chat.db"
PROFILE_DB = pathlib.Path.home() / ".imessage_autoreply_profiles.sqlite3"

# Polling settings
POLL_SECONDS = 5

# NWS API settings
NWS_USER_AGENT = "imessage-autoreply-bot/1.3 (claudep; contact: you@example.com)"

# Welcome back settings
WELCOME_BACK_GAP_SECONDS = 15 * 60  # 15 minutes

# Geocoding defaults
DEFAULT_COUNTRY_CODE = "US"   # prefer US results for city/state texts like "Davis, CA"
GEOCODE_TIMEOUT = 10

