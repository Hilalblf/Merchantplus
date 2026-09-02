import os
import asyncio
import logging
import sqlite3
import re

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# LEX AUTO PUBLISHER PRO - BOT 2
# ============================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]

BOT_NAME = os.getenv("BOT_NAME", "BOT_2")

OWNER_ID = os.getenv("OWNER_ID", "")
OWNER_IDS = {
    int(x.strip())
    for x in OWNER_ID.split(",")
    if x.strip()
}

SOURCE_CHAT_ID = int(os.environ["SOURCE_CHAT_ID"])

TARGET_CHAT_IDS = [
    int(x.strip())
    for x in os.environ["TARGET_CHAT_IDS"].split(",")
    if x.strip()
]

# ============================================================
# DATABASE - BOT 2
# ============================================================

DB_FILE = "lex_publisher_2.db"

# ============================================================
# SPECIAL CHANNELS
# ============================================================

SPECIAL_CHANNELS = {
    -1002239341307: 5578623360,
    -1002895996910: 1760181851,
}

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("LEX-BOT-2")

# ============================================================
# DATABASE
# ============================================================

def db_connect():

    return sqlite3.connect(
        DB_FILE,
        timeout=30
    )


def init_db():

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS message_map (
            source_chat_id INTEGER NOT NULL,
            source_message_id INTEGER NOT NULL,
            target_chat_id INTEGER NOT NULL,
            target_message_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (
                source_chat_id,
                source_message_id,
                target_chat_id
            )
        )
    """)

    conn.commit()
    conn.close()

    logger.info(
        "DATABASE READY: %s",
        DB_FILE
    )


def save_mapping(
    source_chat_id,
    source_message_id,
    target_chat_id,
    target_message_id
):

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        INSERT OR REPLACE INTO message_map
        (
            source_chat_id,
            source_message_id,
            target_chat_id,
            target_message_id
        )
        VALUES (?, ?, ?, ?)
    """, (
        source_chat_id,
        source_message_id,
        target_chat_id,
        target_message_id
    ))

    conn.commit()
    conn.close()


def get_mappings(
    source_message_id,
    source_chat_id
):

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            target_chat_id,
            target_message_id
        FROM message_map
        WHERE
            source_chat_id = ?
            AND source_message_id = ?
    """, (
        source_chat_id,
        source_message_id
    ))

    rows = cur.fetchall()

    conn.close()

    return rows


def get_parent_mapping(
    source_chat_id,
    source_message_id,
    target_chat_id
):

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT target_message_id
        FROM message_map
        WHERE
            source_chat_id = ?
            AND source_message_id = ?
            AND target_chat_id = ?
    """, (
        source_chat_id,
        source_message_id,
        target_chat_id
    ))

    row = cur.fetchone()

    conn.close()

    if row:
        return row[0]

    return None


def delete_mappings(
    source_message_id,
    source_chat_id
):

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM message_map
        WHERE
            source_chat_id = 
