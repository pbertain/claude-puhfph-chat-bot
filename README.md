# claude-puhfph-chat-bot

An iMessage bot for macOS that provides weather forecasts and scheduled messages.

## Description

This bot listens for incoming iMessages and can:
- Provide weather forecasts based on user location
- Schedule recurring weather reports (e.g., daily at 7am)
- Manage conversation state and user profiles

## Requirements

- Python 3.8 or higher
- macOS (for iMessage and Contacts integration)
- Access to Messages database (typically requires full disk access)

## Installation

1. Clone the repository:
```bash
git clone https://github.com/pbertain/claude-puhfph-chat-bot.git
cd claude-puhfph-chat-bot
```

2. Create a virtual environment:
```bash
python3 -m venv venv
```

3. Activate the virtual environment:
```bash
source venv/bin/activate
```

4. Install the project and dependencies:
```bash
pip install -e .
```

Or if you prefer using requirements.txt (for older pip versions):
```bash
pip install -r requirements.txt
```

## Usage

1. Make sure you've activated the virtual environment:
```bash
source venv/bin/activate
```

2. Run the bot:
```bash
python3 imessage-listener.py
```

3. The bot will:
   - Initialize the database on first run
   - Poll for incoming iMessages every 3 seconds
   - Respond to commands and scheduled messages

### Commands

Users can send these commands via iMessage:
- `help` or `?` - Show help text
- `weather` or `wx` - Get current forecast (requires location to be set)
- `I'm in <place> now` - Update location and get forecast (e.g., "I'm in Davis, CA now")
- `send me the weather at 7am everyday` - Schedule daily weather reports
- `send me the weather at 7:30pm daily` - Schedule daily weather at specific time
- `send me the weather at 7am once` - Schedule one-time weather report

### First-time Setup

On first contact, the bot will ask for:
1. First name
2. Last name
3. Location (city and state, e.g., "Davis, CA")

After setup, users can request weather or schedule messages.

## Project Structure

- `imessage-listener.py` - Main entry point
- `config.py` - Configuration constants
- `applescript_helpers.py` - iMessage and Contacts integration
- `database.py` - SQLite database operations
- `message_polling.py` - iMessage DB polling
- `geocode.py` - Location geocoding
- `weather.py` - Weather forecast API
- `conversation.py` - Conversation state machine
- `scheduler.py` - Scheduled message functionality

## License

MIT License - see [LICENSE](LICENSE) file for details.



