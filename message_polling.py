#!/usr/bin/env python3
"""
iMessage database polling and state file management.
"""
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import config


@dataclass
class Incoming:
    """Represents an incoming iMessage."""
    rowid: int
    handle_id: str
    text: str


def read_last_rowid() -> int:
    """Read the last processed row ID from the state file."""
    try:
        return int(config.STATE_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return 0


def write_last_rowid(rowid: int) -> None:
    """Write the last processed row ID to the state file."""
    config.STATE_FILE.write_text(str(rowid), encoding="utf-8")


def get_latest_incoming_since(last_rowid: int) -> Optional[Incoming]:
    """Get the latest incoming message since the given row ID."""
    if not config.CHAT_DB.exists():
        raise FileNotFoundError(
            f"Missing Messages DB: {config.CHAT_DB}\n"
            "Make sure Messages app has been used at least once, and grant Full Disk Access "
            "to Terminal (or your Python interpreter) in System Settings > Privacy & Security."
        )

    uri = f"file:{config.CHAT_DB}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError as e:
        if "unable to open database file" in str(e).lower():
            raise PermissionError(
                f"Cannot access Messages database: {config.CHAT_DB}\n"
                "This usually means you need to grant Full Disk Access permission.\n"
                "Go to: System Settings > Privacy & Security > Full Disk Access\n"
                "Add Terminal (or your Python interpreter) and restart the app."
            ) from e
        raise
    con.row_factory = sqlite3.Row

    row = con.execute(
        """
        SELECT
            message.ROWID AS rowid,
            handle.id AS handle_id,
            COALESCE(message.text, '') AS text
        FROM message
        JOIN handle ON handle.ROWID = message.handle_id
        WHERE message.is_from_me = 0
          AND message.ROWID > ?
        ORDER BY message.date DESC
        LIMIT 1
        """,
        (last_rowid,),
    ).fetchone()
    con.close()

    if row is None:
        return None

    return Incoming(
        rowid=int(row["rowid"]),
        handle_id=str(row["handle_id"]),
        text=str(row["text"] or "").strip(),
    )

