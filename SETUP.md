# Setup Guide for Fresh macOS Installation

## 1. Install Homebrew

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow the on-screen instructions. After installation, you may need to add Homebrew to your PATH:

```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

## 2. Install Python 3.13 (or latest stable)

```bash
brew install python@3.13
```

Or for the latest Python 3.12:
```bash
brew install python@3.12
```

Verify installation:
```bash
python3 --version
```

## 3. Clone/Update Repository

If starting fresh:
```bash
git clone https://github.com/pbertain/claude-puhfph-chat-bot.git
cd claude-puhfph-chat-bot
```

Or if you already have the repo:
```bash
cd claude-puhfph-chat-bot
git pull origin main
```

## 4. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

## 5. Install Dependencies

Using pyproject.toml (recommended):
```bash
pip install --upgrade pip
pip install -e .
```

Or using requirements.txt:
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## 6. Verify Installation

```bash
python3 -m py_compile imessage-listener.py conversation.py database.py scheduler.py
```

## 7. macOS Permissions Setup

**Important:** You'll need to grant Full Disk Access to Terminal (or your Python interpreter) to access the Messages database:

1. Open **System Settings** (or **System Preferences** on older macOS)
2. Go to **Privacy & Security** → **Full Disk Access**
3. Click the **+** button
4. Navigate to `/Applications/Utilities/Terminal.app` and add it
   - Or if using iTerm2: `/Applications/iTerm.app`
   - Or if running Python directly: `/opt/homebrew/bin/python3` (or wherever Python is installed)

## 8. Run the Bot

```bash
python3 imessage-listener.py
```

## Troubleshooting

### Database Locked Error
- Make sure only one instance is running
- Check that Full Disk Access is granted

### SSL Warning
The urllib3 SSL warning is harmless and can be ignored. If you want to suppress it:
```python
import warnings
warnings.filterwarnings('ignore', category=UserWarning, module='urllib3')
```

### Python Version Issues
If you have multiple Python versions, make sure you're using the right one:
```bash
which python3
python3 --version
```

