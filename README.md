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
   - Poll for incoming iMessages every 5 seconds
   - Respond to commands and scheduled messages

### Running with Startup Script

You can use the provided startup script to automatically activate the virtual environment and run the bot:

```bash
./start_bot.sh
```

This script will:
- Automatically activate the virtual environment in the project directory
- Run the bot with proper error handling
- Use the correct working directory

**Note:** If you want to use a different virtual environment path (e.g., `/Users/claudep/tools/venv/bin/activate`), edit `start_bot.sh` and change the `VENV_PATH` variable.

### Running as a Launch Agent (Automatic Startup)

To have the bot start automatically on login and keep running in the background:

1. Install the launchctl service:
```bash
./install_launchctl.sh
```

2. The bot will now:
   - Start automatically when you log in
   - Restart automatically if it crashes
   - Log output to `bot.log` and errors to `bot_error.log`

3. To check if it's running:
```bash
launchctl list | grep claudepuhfph
```

4. To stop the service:
```bash
launchctl unload ~/Library/LaunchAgents/com.claudepuhfph.chatbot.plist
```

5. To start it again:
```bash
launchctl load ~/Library/LaunchAgents/com.claudepuhfph.chatbot.plist
```

6. To uninstall the service:
```bash
./uninstall_launchctl.sh
```

**Important:** Make sure Terminal (or your Python interpreter) has Full Disk Access permissions in System Settings > Privacy & Security, otherwise the bot won't be able to access the Messages database.

### Web Troubleshooting Interface

A web-based troubleshooting dashboard is available to monitor the bot's status and diagnose issues:

1. Start the troubleshooting server:
```bash
python3 web_troubleshooting.py
```

2. Open your browser to:
```
http://localhost:55042
```

The dashboard shows:
- Bot process status (running/stopped)
- Launchctl service status
- Database access status
- Database statistics (users, scheduled messages, alarms)
- Recent logs and errors
- Last processed message row ID
- Scheduled messages and alarms

The server also provides JSON API endpoints:
- `GET /api/status` - Bot status information
- `GET /api/stats` - Database statistics
- `GET /api/logs` - Recent log entries

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
- `start_bot.sh` - Startup script that activates venv and runs the bot
- `install_launchctl.sh` - Script to install launchctl service
- `uninstall_launchctl.sh` - Script to uninstall launchctl service
- `com.claudepuhfph.chatbot.plist` - Launchctl plist configuration
- `web_troubleshooting.py` - Web-based troubleshooting dashboard (runs on port 55042)

## License

MIT License - see [LICENSE](LICENSE) file for details.



