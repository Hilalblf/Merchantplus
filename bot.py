import os
import asyncio
import logging
import sqlite3

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

# اسم أو معرف البوت للتمييز في اللوغات
BOT_NAME = os.getenv("BOT_NAME", "BOT_2")

# ============================================================
# ALLOWED USERS
# ============================================================

ALLOWED_USER_IDS = [
    int(x.strip())
    for x in os.environ["OWNER_ID"].split(",")
    if x.strip()
]


def is_allowed(user_id):
    return user_id in ALLOWED_USER_IDS


# ============================================================
# الصديق: ينشر رسائله في نفس الـ 5 مجموعات + قناة إضافية خاصة به فقط
# ============================================================

FRIEND_USER_ID = 1154384855
FRIEND_CHANNEL_ID = -1001716893195

# ============================================================
# SOURCE & TARGETS
# ============================================================

SOURCE_CHAT_ID = int(os.environ["SOURCE_CHAT_ID"])

# قناة إضافية: النشر منها مسموح فقط للقناة الخاصة بهذا الشخص
SPECIAL_CHANNELS = {
    -1002239341307: 5578623360,
    -1002895996910: 1760181851,
}

TARGET_CHAT_IDS = [
    int(x.strip())
    for x in os.environ["TARGET_CHAT_IDS"].split(",")
    if x.strip()
]

# ============================================================
# DATABASE (مستقلة خاصة بالبوت الثاني)
# ============================================================

DB_FILE = os.getenv(
    "DB_FILE",
    "lex_publisher_2.db"
)

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s | [{BOT_NAME}] | %(levelname)s | %(message)s"
)

logger = logging.getLogger(BOT_NAME)


# ============================================================
# DATABASE INIT
# ============================================================

def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS message_map (
                source_chat_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL,
                target_chat_id INTEGER NOT NULL,
                target_message_id INTEGER NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (
                    source_chat_id,
                    source_message_id,
                    target_chat_id
                )
            )
        """)
        conn.commit()
    finally:
        conn.close()


def save_mapping(source_message_id, target_chat_id, target_message_id, source_chat_id=SOURCE_CHAT_ID):
    conn = sqlite3.connect(DB_FILE, timeout=30)
    try:
        conn.execute("""
            INSERT OR REPLACE INTO message_map (
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
    finally:
        conn.close()


def get_mappings(source_message_id, source_chat_id=SOURCE_CHAT_ID):
    conn = sqlite3.connect(DB_FILE, timeout=30)
    try:
        return conn.execute("""
            SELECT target_chat_id, target_message_id
            FROM message_map
            WHERE source_chat_id = ? AND source_message_id = ?
            ORDER BY target_chat_id
        """, (
            source_chat_id,
            source_message_id
        )).fetchall()
    finally:
        conn.close()


def delete_mappings(source_message_id, source_chat_id=SOURCE_CHAT_ID):
    conn = sqlite3.connect(DB_FILE, timeout=30)
    try:
        conn.execute("""
            DELETE FROM message_map
            WHERE source_chat_id = ? AND source_message_id = ?
        """, (
            source_chat_id,
            source_message_id
        ))
        conn.commit()
    finally:
        conn.close()


# ============================================================
# TELETHON CLIENT
# ============================================================

client = TelegramClient(
    "lex_publisher_session_2",
    API_ID,
    API_HASH
)

BOT_ID = None


# ============================================================
# RETRY HELPER
# ============================================================

async def run_with_retry(func, *args, **kwargs):
    for attempt in range(3):
        try:
            return await func(*args, **kwargs)
        except FloodWaitError as e:
            logger.warning("FloodWait: %s seconds",
