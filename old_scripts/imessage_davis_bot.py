#!/usr/bin/env python3
import time
import logging
from pathlib import Path

import requests
import applescript  # py-applescript [web:27][web:39]

BASE_DIR = Path(__file__).resolve().parent
SCRIPT_PATH = BASE_DIR / "imessage_agent.applescript"
LOG_PATH = BASE_DIR / "imessage-bot.log"

logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

POLL_SECONDS = 3


def load_script():
    with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
        src = f.read()
    # compiled once and kept in memory [web:27][web:40]
    return applescript.AppleScript(source=src)


def get_weather():
    # One-line weather for Davis, CA in US units [web:9][web:12][web:33]
    try:
        resp = requests.get("https://wttr.in/Davis,CA?format=3&u", timeout=5)
        resp.raise_for_status()
        return resp.text.strip()
    except Exception as e:
        logging.warning("Weather lookup failed: %s", e)
        return "Weather lookup failed"


def main():
    logging.info("Starting iMessage Davis bot")
    scpt = load_script()

    while True:
        try:
            result = scpt.call("poll_new_message")
            if result:
                # py-applescript returns a dict-like object for AppleScript records [web:27]
                first_name = result.get("first_name")
                greeting = result.get("greeting")
                date_str = result.get("date_str")
                chat_id = result.get("chat_id")
                msg_id = result.get("msg_id")

                if not all([first_name, greeting, date_str, chat_id, msg_id]):
                    logging.debug("Incomplete payload from AppleScript: %r", result)
                    time.sleep(POLL_SECONDS)
                    continue

                wx = get_weather()
                reply_text = (
                    f"Hi {first_name}. {greeting} on {date_str}. "
                    f"The weather for Davis, CA is:\n\n{wx}"
                )

                scpt.call("send_reply", chat_id, reply_text)
                logging.info(
                    "Replied to %s in chat %s (msg %s)",
                    first_name,
                    chat_id,
                    msg_id,
                )

            time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            logging.info("KeyboardInterrupt, exiting")
            break
        except Exception as e:
            logging.exception("Unhandled error in main loop: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()

