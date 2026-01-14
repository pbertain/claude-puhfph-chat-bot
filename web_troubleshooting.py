#!/usr/bin/env python3
"""
Web troubleshooting interface for the iMessage bot.
Runs on port 55042 and provides status, logs, and diagnostics.
"""
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, render_template_string, jsonify

import config
import database
import message_polling
import scheduler

app = Flask(__name__)


def check_bot_running() -> bool:
    """Check if the bot process is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "imessage-listener.py"],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def check_launchctl_running() -> bool:
    """Check if launchctl service is running."""
    try:
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
        )
        return "claudepuhfph" in result.stdout
    except Exception:
        return False


def get_recent_logs(log_file: Path, lines: int = 50) -> list[str]:
    """Get recent lines from a log file."""
    if not log_file.exists():
        return []
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
            return all_lines[-lines:] if len(all_lines) > lines else all_lines
    except Exception as e:
        return [f"Error reading log: {e}"]


def get_database_stats() -> dict:
    """Get statistics from the database."""
    def _do():
        con = database.db_connect()
        
        # Count users
        user_count = con.execute("SELECT COUNT(*) FROM person").fetchone()[0]
        
        # Count scheduled messages
        scheduled_count = con.execute("SELECT COUNT(*) FROM scheduled_messages").fetchone()[0]
        
        # Count alarms
        alarm_count = con.execute("SELECT COUNT(*) FROM alarms").fetchone()[0]
        
        # Count conversation states
        state_counts = {}
        states = con.execute("SELECT state, COUNT(*) as count FROM convo_state GROUP BY state").fetchall()
        for state, count in states:
            state_counts[state] = count
        
        # Get recent users (last seen)
        recent_users = con.execute(
            """
            SELECT handle_id, first_name, last_name, last_seen_at
            FROM person
            ORDER BY last_seen_at DESC
            LIMIT 10
            """
        ).fetchall()
        
        con.close()
        
        return {
            "user_count": user_count,
            "scheduled_count": scheduled_count,
            "alarm_count": alarm_count,
            "state_counts": dict(state_counts),
            "recent_users": [
                {
                    "handle_id": row[0],
                    "first_name": row[1],
                    "last_name": row[2],
                    "last_seen_at": row[3],
                }
                for row in recent_users
            ],
        }
    
    try:
        return database.db_exec(_do)
    except Exception as e:
        return {"error": str(e)}


def get_scheduled_messages_info() -> list[dict]:
    """Get information about scheduled messages."""
    def _do():
        con = database.db_connect()
        rows = con.execute(
            """
            SELECT schedule_id, handle_id, message_type, schedule_time, schedule_type, next_run_at
            FROM scheduled_messages
            ORDER BY next_run_at ASC
            LIMIT 20
            """
        ).fetchall()
        con.close()
        
        return [
            {
                "schedule_id": row[0],
                "handle_id": row[1],
                "message_type": row[2],
                "schedule_time": row[3],
                "schedule_type": row[4],
                "next_run_at": row[5],
            }
            for row in rows
        ]
    
    try:
        return database.db_exec(_do)
    except Exception as e:
        return [{"error": str(e)}]


def get_alarms_info() -> list[dict]:
    """Get information about alarms."""
    def _do():
        con = database.db_connect()
        rows = con.execute(
            """
            SELECT alarm_id, handle_id, alarm_title, alert_time, schedule_type, next_run_at
            FROM alarms
            ORDER BY next_run_at ASC
            LIMIT 20
            """
        ).fetchall()
        con.close()
        
        return [
            {
                "alarm_id": row[0],
                "handle_id": row[1],
                "alarm_title": row[2],
                "alert_time": row[3],
                "schedule_type": row[4],
                "next_run_at": row[5],
            }
            for row in rows
        ]
    
    try:
        return database.db_exec(_do)
    except Exception as e:
        return [{"error": str(e)}]


@app.route("/")
def index():
    """Main troubleshooting dashboard."""
    bot_running = check_bot_running()
    launchctl_running = check_launchctl_running()
    last_rowid = message_polling.read_last_rowid()
    
    # Get log files
    script_dir = Path(__file__).parent
    bot_log = script_dir / "bot.log"
    bot_error_log = script_dir / "bot_error.log"
    
    recent_logs = get_recent_logs(bot_log, 30)
    recent_errors = get_recent_logs(bot_error_log, 30)
    
    db_stats = get_database_stats()
    scheduled_messages = get_scheduled_messages_info()
    alarms = get_alarms_info()
    
    # Check if Messages DB is accessible
    messages_db_accessible = config.CHAT_DB.exists()
    profile_db_accessible = config.PROFILE_DB.exists()
    
    return render_template_string(HTML_TEMPLATE, **{
        "bot_running": bot_running,
        "launchctl_running": launchctl_running,
        "last_rowid": last_rowid,
        "recent_logs": recent_logs,
        "recent_errors": recent_errors,
        "db_stats": db_stats,
        "scheduled_messages": scheduled_messages,
        "alarms": alarms,
        "messages_db_accessible": messages_db_accessible,
        "profile_db_accessible": profile_db_accessible,
        "messages_db_path": str(config.CHAT_DB),
        "profile_db_path": str(config.PROFILE_DB),
        "poll_seconds": config.POLL_SECONDS,
        "state_file": str(config.STATE_FILE),
    })


@app.route("/api/status")
def api_status():
    """JSON API endpoint for status."""
    return jsonify({
        "bot_running": check_bot_running(),
        "launchctl_running": check_launchctl_running(),
        "last_rowid": message_polling.read_last_rowid(),
        "messages_db_accessible": config.CHAT_DB.exists(),
        "profile_db_accessible": config.PROFILE_DB.exists(),
        "poll_seconds": config.POLL_SECONDS,
    })


@app.route("/api/stats")
def api_stats():
    """JSON API endpoint for database stats."""
    return jsonify(get_database_stats())


@app.route("/api/logs")
def api_logs():
    """JSON API endpoint for recent logs."""
    script_dir = Path(__file__).parent
    bot_log = script_dir / "bot.log"
    bot_error_log = script_dir / "bot_error.log"
    
    return jsonify({
        "stdout": get_recent_logs(bot_log, 100),
        "stderr": get_recent_logs(bot_error_log, 100),
    })


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>iMessage Bot Troubleshooting</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: #f5f5f5;
            padding: 20px;
            color: #333;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { margin-bottom: 30px; color: #2c3e50; }
        .status-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .card {
            background: white;
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .card h2 {
            font-size: 18px;
            margin-bottom: 15px;
            color: #34495e;
            border-bottom: 2px solid #3498db;
            padding-bottom: 10px;
        }
        .status-item {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #eee;
        }
        .status-item:last-child { border-bottom: none; }
        .status-badge {
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: bold;
        }
        .status-running { background: #2ecc71; color: white; }
        .status-stopped { background: #e74c3c; color: white; }
        .status-ok { background: #27ae60; color: white; }
        .status-error { background: #e74c3c; color: white; }
        .log-container {
            background: #1e1e1e;
            color: #d4d4d4;
            padding: 15px;
            border-radius: 4px;
            font-family: 'Monaco', 'Menlo', 'Courier New', monospace;
            font-size: 12px;
            max-height: 300px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        .log-error { color: #f48771; }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }
        th, td {
            padding: 10px;
            text-align: left;
            border-bottom: 1px solid #eee;
        }
        th {
            background: #f8f9fa;
            font-weight: 600;
            color: #495057;
        }
        tr:hover { background: #f8f9fa; }
        .refresh-btn {
            background: #3498db;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
            margin-bottom: 20px;
        }
        .refresh-btn:hover { background: #2980b9; }
        .timestamp { color: #7f8c8d; font-size: 11px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 iMessage Bot Troubleshooting Dashboard</h1>
        
        <button class="refresh-btn" onclick="location.reload()">🔄 Refresh</button>
        
        <div class="status-grid">
            <div class="card">
                <h2>Bot Status</h2>
                <div class="status-item">
                    <span>Process Running:</span>
                    <span class="status-badge {% if bot_running %}status-running{% else %}status-stopped{% endif %}">
                        {% if bot_running %}Running{% else %}Stopped{% endif %}
                    </span>
                </div>
                <div class="status-item">
                    <span>Launchctl Service:</span>
                    <span class="status-badge {% if launchctl_running %}status-running{% else %}status-stopped{% endif %}">
                        {% if launchctl_running %}Running{% else %}Stopped{% endif %}
                    </span>
                </div>
                <div class="status-item">
                    <span>Last Processed RowID:</span>
                    <span><strong>{{ last_rowid }}</strong></span>
                </div>
                <div class="status-item">
                    <span>Poll Interval:</span>
                    <span><strong>{{ poll_seconds }} seconds</strong></span>
                </div>
            </div>
            
            <div class="card">
                <h2>Database Access</h2>
                <div class="status-item">
                    <span>Messages DB:</span>
                    <span class="status-badge {% if messages_db_accessible %}status-ok{% else %}status-error{% endif %}">
                        {% if messages_db_accessible %}Accessible{% else %}Not Found{% endif %}
                    </span>
                </div>
                <div class="status-item">
                    <span>Profile DB:</span>
                    <span class="status-badge {% if profile_db_accessible %}status-ok{% else %}status-error{% endif %}">
                        {% if profile_db_accessible %}Accessible{% else %}Not Found{% endif %}
                    </span>
                </div>
                <div class="status-item">
                    <span>Messages DB Path:</span>
                    <span class="timestamp">{{ messages_db_path }}</span>
                </div>
                <div class="status-item">
                    <span>Profile DB Path:</span>
                    <span class="timestamp">{{ profile_db_path }}</span>
                </div>
            </div>
            
            <div class="card">
                <h2>Database Statistics</h2>
                {% if db_stats.error %}
                <div class="status-item">
                    <span style="color: #e74c3c;">Error: {{ db_stats.error }}</span>
                </div>
                {% else %}
                <div class="status-item">
                    <span>Total Users:</span>
                    <span><strong>{{ db_stats.user_count }}</strong></span>
                </div>
                <div class="status-item">
                    <span>Scheduled Messages:</span>
                    <span><strong>{{ db_stats.scheduled_count }}</strong></span>
                </div>
                <div class="status-item">
                    <span>Alarms:</span>
                    <span><strong>{{ db_stats.alarm_count }}</strong></span>
                </div>
                {% if db_stats.state_counts %}
                <div class="status-item">
                    <span>Conversation States:</span>
                    <span>
                        {% for state, count in db_stats.state_counts.items() %}
                            {{ state }}: {{ count }}{% if not loop.last %}, {% endif %}
                        {% endfor %}
                    </span>
                </div>
                {% endif %}
                {% endif %}
            </div>
        </div>
        
        {% if db_stats.recent_users %}
        <div class="card" style="margin-bottom: 30px;">
            <h2>Recent Users</h2>
            <table>
                <thead>
                    <tr>
                        <th>Handle ID</th>
                        <th>Name</th>
                        <th>Last Seen</th>
                    </tr>
                </thead>
                <tbody>
                    {% for user in db_stats.recent_users %}
                    <tr>
                        <td>{{ user.handle_id }}</td>
                        <td>{{ user.first_name or '' }} {{ user.last_name or '' }}</td>
                        <td class="timestamp">{{ user.last_seen_at }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% endif %}
        
        {% if scheduled_messages %}
        <div class="card" style="margin-bottom: 30px;">
            <h2>Scheduled Messages</h2>
            <table>
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Handle ID</th>
                        <th>Type</th>
                        <th>Schedule Time</th>
                        <th>Schedule Type</th>
                        <th>Next Run</th>
                    </tr>
                </thead>
                <tbody>
                    {% for msg in scheduled_messages %}
                    <tr>
                        <td>{{ msg.schedule_id }}</td>
                        <td>{{ msg.handle_id }}</td>
                        <td>{{ msg.message_type }}</td>
                        <td>{{ msg.schedule_time or 'N/A' }}</td>
                        <td>{{ msg.schedule_type }}</td>
                        <td class="timestamp">{{ msg.next_run_at }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% endif %}
        
        {% if alarms %}
        <div class="card" style="margin-bottom: 30px;">
            <h2>Alarms</h2>
            <table>
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Handle ID</th>
                        <th>Title</th>
                        <th>Alert Time</th>
                        <th>Schedule Type</th>
                        <th>Next Run</th>
                    </tr>
                </thead>
                <tbody>
                    {% for alarm in alarms %}
                    <tr>
                        <td>{{ alarm.alarm_id }}</td>
                        <td>{{ alarm.handle_id }}</td>
                        <td>{{ alarm.alarm_title }}</td>
                        <td>{{ alarm.alert_time }}</td>
                        <td>{{ alarm.schedule_type }}</td>
                        <td class="timestamp">{{ alarm.next_run_at }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% endif %}
        
        <div class="card" style="margin-bottom: 30px;">
            <h2>Recent Logs (stdout)</h2>
            <div class="log-container">
                {% if recent_logs %}
                    {% for line in recent_logs %}
                        {{ line }}
                    {% endfor %}
                {% else %}
                    No logs available
                {% endif %}
            </div>
        </div>
        
        <div class="card">
            <h2>Recent Errors (stderr)</h2>
            <div class="log-container">
                {% if recent_errors %}
                    {% for line in recent_errors %}
                        <span class="log-error">{{ line }}</span>
                    {% endfor %}
                {% else %}
                    No errors
                {% endif %}
            </div>
        </div>
    </div>
</body>
</html>
"""


def main():
    """Run the web troubleshooting server."""
    import socket
    
    # Get LAN IP address
    lan_ip = "localhost"
    try:
        # Connect to a remote address to determine the local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    
    print(f"Starting troubleshooting web interface:")
    print(f"  Local: http://localhost:55042")
    print(f"  LAN:   http://{lan_ip}:55042")
    print("Press Ctrl-C to stop")
    # Listen on all interfaces (0.0.0.0) to allow LAN access via en0
    app.run(host="0.0.0.0", port=55042, debug=False)


if __name__ == "__main__":
    main()
