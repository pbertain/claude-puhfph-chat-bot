# Setup Guide

## Branches

Standard deployments always run from the `main` branch.

```bash
git pull origin main
```

If you are testing a feature or development branch, adjust accordingly
(`git checkout dev`, etc.), but treat `main` as the stable deployment target.

---

## Python: use Homebrew, not the macOS system Python

The bot requires **macOS Full Disk Access** (to read `~/Library/Messages/chat.db`) and
**Automation permission** (to send Apple Events to Messages and Contacts). macOS will not
grant those TCC permissions to the system Python at `/usr/bin/python3`.

Use a Homebrew-managed Python instead:

```bash
# Install Homebrew if you don't have it
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Add Homebrew to PATH (Apple Silicon Macs)
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"

# Install Python (3.11 or later recommended)
brew install python@3.11

# Verify
python3.11 --version
```

If you already created a venv with the system Python, delete it and recreate it:

```bash
rm -rf venv
python3.11 -m venv venv
```

---

## Fresh install

### 1. Clone the repo

```bash
git clone https://github.com/pbertain/claude-puhfph-chat-bot.git
cd claude-puhfph-chat-bot
```

### 2. Create the virtual environment

```bash
python3.11 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install --upgrade pip
pip install -e .
```

### 4. Verify

```bash
python3 -m py_compile imessage-listener.py conversation.py database.py scheduler.py
```

### 5. Grant macOS permissions

The bot needs two categories of permission. Both must be granted to the **venv Python
binary** (e.g. `~/tools/claude-puhfph-chat-bot/venv/bin/python3`), not to Terminal.

Run the bot once manually first — it will print the exact path and tell you what is missing:

```bash
source venv/bin/activate
python3 imessage-listener.py
```

| Permission | Location | What to add |
|---|---|---|
| Full Disk Access | System Settings > Privacy & Security > Full Disk Access | The venv `python3` binary |
| Automation → Messages | System Settings > Privacy & Security > Automation | The venv `python3` → Messages |
| Automation → Contacts | System Settings > Privacy & Security > Automation | The venv `python3` → Contacts |

After granting permissions, restart the bot.

### 6. Install as a launchd service (optional)

```bash
./scripts/install_launchctl.sh       # main bot
./scripts/install_web_launchctl.sh   # web status UI (optional)
```

---

## Upgrading an existing deployment

When pulling new code, the launchd services must be reinstalled because the plist
references the startup scripts by absolute path.

```bash
# 1. Stop and remove existing services
./scripts/uninstall_launchctl.sh
./scripts/uninstall_web_launchctl.sh   # if installed

# 2. Pull latest code
git pull origin main

# 3. Update dependencies (if any changed)
source venv/bin/activate
pip install -e .

# 4. Reinstall services
./scripts/install_launchctl.sh
./scripts/install_web_launchctl.sh   # if needed

# 5. Confirm running
launchctl list | grep claudepuhfph
tail -f bot.log
```

---

## Troubleshooting

### "authorization denied" / Apple Events errors

The venv Python hasn't been granted Automation permission. Run the bot manually — it
prints the exact binary path and fix instructions. Then grant it in
System Settings > Privacy & Security > Automation.

### "unable to open database file" / chat.db errors

The venv Python hasn't been granted Full Disk Access. Add it in
System Settings > Privacy & Security > Full Disk Access.

### Database locked

Only one instance of the bot should be running. Check with:

```bash
pgrep -fl imessage-listener
```

### Wrong Python version

```bash
which python3
python3 --version   # should be 3.9 or later, ideally 3.11+
```
