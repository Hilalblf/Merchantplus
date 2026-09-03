import os
import logging
import sqlite3
import asyncio

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# LEX AUTO PUBLISHER PRO - BOT 2
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# ============================================================
# ENV PARSERS
# ============================================================

def get_required(name):
    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(f"❌ Missing required Variable: {name}")

    return value


def get_int(name, default=None):
    value = os.getenv(name, "").strip()

    if not value:
        if default is not None:
            return default
        raise RuntimeError(f"❌ Missing required Variable: {name}")

    try:
        return int(value)
    except ValueError:
        raise RuntimeError(
            f"❌ Variable {name} must be a single integer, got: {value}"
        )


def parse_int_list(value):
    """
    مثال:
    123,456,789
    """
    result = set()

    if not value:
        return result

    for item in value.split(","):
        item = item.strip()

        if not item:
            continue

        try:
            result.add(int(item))
        except ValueError:
            logging.warning(
                "⚠️ Invalid ID ignored from list: %s",
                item
            )

    return result


# ============================================================
# BASIC VARIABLES
# ============================================================

API_ID = get_int("API_ID")
API_HASH = get_required("API_HASH")
BOT_TOKEN = get_required("BOT_TOKEN")

DB_FILE = os.getenv("DB_FILE", "lex_publisher_2.db").strip()

OWNER_ID = get_int("OWNER_ID")

SOURCE_CHAT_ID = get_int("SOURCE_CHAT_ID")

TARGET_CHAT_IDS = parse_int_list(
    os.getenv("TARGET_CHAT_IDS", "")
)

if not TARGET_CHAT_IDS:
    raise RuntimeError(
        "❌ TARGET_CHAT_IDS is empty."
    )


# ============================================================
# ADMIN IDS
# ============================================================

ADMIN_IDS = parse_int_list(
    os.getenv("ADMIN_IDS", "")
)

# OWNER is always admin
ADMIN_IDS.add(OWNER_ID)


# ============================================================
# PERSONAL CHANNELS
#
# Format:
# USER_ID:CHANNEL_ID;USER_ID:CHANNEL_ID
# ============================================================

def parse_personal_channels(value):
    result = {}

    if not value:
        return result

    entries = value.split(";")

    for entry in entries:
        entry = entry.strip()

        if not entry:
            continue

        if ":" not in entry:
            logging.warning(
                "⚠️ Invalid PERSONAL_CHANNELS entry: %s",
                entry
            )
            continue

        user_part, channel_part = entry.split(":", 1)

        try:
            user_id = int(user_part.strip())
            channel_id = int(channel_part.strip())

            result[user_id] = channel_id

        except ValueError:
            logging.warning(
                "⚠️ Invalid PERSONAL_CHANNELS entry: %s",
                entry
            )

    return result


PERSONAL_CHANNELS = parse_personal_channels(
    os.getenv("PERSONAL_CHANNELS", "")
)


# ============================================================
# USER BLOCKED TARGETS
#
# Format:
# USER_ID:TARGET1,TARGET2;USER_ID:TARGET3
# ============================================================

def parse_user_blocked_targets(value):
    result = {}

    if not value:
        return result

    entries = value.split(";")

    for entry in entries:
        entry = entry.strip()

        if not entry:
            continue

        if ":" not in entry:
            logging.warning(
                "⚠️ Invalid USER_BLOCKED_TARGETS entry: %s",
                entry
            )
            continue

        user_part, targets_part = entry.split(":", 1)

        try:
            user_id = int(user_part.strip())
        except ValueError:
            logging.warning(
                "⚠️ Invalid blocked user ID: %s",
                user_part
            )
            continue

        blocked_targets = set()

        for target in targets_part.split(","):
            target = target.strip()

            if not target:
                continue

            try:
                blocked_targets.add(int(target))
            except ValueError:
                logging.warning(
                    "⚠️ Invalid blocked target ID: %s",
                    target
                )

        if blocked_targets:
            result[user_id] = blocked_targets

    return result


USER_BLOCKED_TARGETS = parse_user_blocked_targets(
    os.getenv("USER_BLOCKED_TARGETS", "")
)


# ============================================================
# SHOW CONFIG
# ============================================================

logging.info("========================================")
logging.info("LEX AUTO PUBLISHER PRO - BOT 2")
logging.info("========================================")

logging.info("Source: %s", SOURCE_CHAT_ID)
logging.info("Regular targets: %s", TARGET_CHAT_IDS)
logging.info("Admins loaded: %s", len(ADMIN_IDS))
logging.info("Personal channels: %s", PERSONAL_CHANNELS)
logging.info("Blocked targets: %s", USER_BLOCKED_TARGETS)

logging.info("========================================")


# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(
    DB_FILE,
    check_same_thread=False
)

db.execute("""
CREATE TABLE IF NOT EXISTS published_messages (
    source_msg_id INTEGER NOT NULL,
    target_chat_id INTEGER NOT NULL,
    target_msg_id INTEGER NOT NULL,
    PRIMARY KEY (source_msg_id, target_chat_id)
)
""")

db.commit()


# ============================================================
# DATABASE HELPERS
# ============================================================

def save_mapping(source_msg_id, target_chat_id, target_msg_id):
    db.execute(
        """
        INSERT OR REPLACE INTO published_messages
        (
            source_msg_id,
            target_chat_id,
            target_msg_id
        )
        VALUES (?, ?, ?)
        """,
        (
            source_msg_id,
            target_chat_id,
            target_msg_id
        )
    )

    db.commit()


def get_mappings(source_msg_id):
    cursor = db.execute(
        """
        SELECT target_chat_id, target_msg_id
        FROM published_messages
        WHERE source_msg_id = ?
        """,
        (source_msg_id,)
    )

    return cursor.fetchall()


def delete_mapping(source_msg_id, target_chat_id=None):
    if target_chat_id is None:
        db.execute(
            """
            DELETE FROM published_messages
            WHERE source_msg_id = ?
            """,
            (source_msg_id,)
        )
    else:
        db.execute(
            """
            DELETE FROM published_messages
            WHERE source_msg_id = ?
            AND target_chat_id = ?
            """,
            (
                source_msg_id,
                target_chat_id
            )
        )

    db.commit()


def get_source_by_target(target_chat_id, target_msg_id):
    cursor = db.execute(
        """
        SELECT source_msg_id
        FROM published_messages
        WHERE target_chat_id = ?
        AND target_msg_id = ?
        """,
        (
            target_chat_id,
            target_msg_id
        )
    )

    row = cursor.fetchone()

    if row:
        return row[0]

    return None


# ============================================================
# TARGETS FOR USER
# ============================================================

def get_targets_for_user(user_id):
    """
    يرجع القنوات التي يسمح لهذا المستخدم بالنشر فيها.
    """

    targets = list(TARGET_CHAT_IDS)

    # Personal channel
    personal_channel = PERSONAL_CHANNELS.get(user_id)

    if personal_channel:
        if personal_channel not in targets:
            targets.append(personal_channel)

    # Remove blocked targets
    blocked = USER_BLOCKED_TARGETS.get(user_id, set())

    targets = [
        target
        for target in targets
        if target not in blocked
    ]

    # Remove duplicates
    targets = list(dict.fromkeys(targets))

    return targets


# ============================================================
# TELEGRAM CLIENT
# ============================================================

client = TelegramClient(
    "lex_publisher_2_session",
    API_ID,
    API_HASH
)


# ============================================================
# PUBLISH MESSAGE
# ============================================================

async def publish_message(message, sender_id):
    targets = get_targets_for_user(sender_id)

    if not targets:
        logging.info(
            "User %s has no allowed targets.",
            sender_id
        )
        return

    logging.info(
        "Publishing message %s from user %s to %s targets",
        message.id,
        sender_id,
        len(targets)
    )

    reply_target_map = {}

    # ========================================================
    # CHECK SOURCE REPLY
    # ========================================================

    if message.is_reply:
        try:
            replied_source = await message.get_reply_message()

            if replied_source:
                mappings = get_mappings(
                    replied_source.id
                )

                for target_chat_id, target_msg_id in mappings:
                    reply_target_map[target_chat_id] = target_msg_id

        except Exception as e:
            logging.warning(
                "⚠️ Could not resolve reply: %s",
                e
            )

    # ========================================================
    # SEND TO TARGETS
    # ========================================================

    for target_chat_id in targets:

        try:

            reply_to = reply_target_map.get(
                target_chat_id
            )

            sent = await client.send_message(
                target_chat_id,
                message,
                reply_to=reply_to
            )

            save_mapping(
                message.id,
                target_chat_id,
                sent.id
            )

            logging.info(
                "✅ Published %s -> %s:%s",
                message.id,
                target_chat_id,
                sent.id
            )

        except FloodWaitError as e:

            logging.warning(
                "⏳ FloodWait %s seconds",
                e.seconds
            )

            await asyncio.sleep(
                e.seconds
            )

        except Exception as e:

            logging.error(
                "❌ Failed target %s: %s",
                target_chat_id,
                e
            )


# ============================================================
# NEW SOURCE MESSAGE
# ============================================================

@client.on(
    events.NewMessage(
        chats=SOURCE_CHAT_ID
    )
)
async def new_message_handler(event):

    try:

        sender_id = event.sender_id

        if sender_id is None:
            sender_id = 0

        await publish_message(
            event.message,
            sender_id
        )

    except Exception as e:

        logging.exception(
            "❌ New message handler error: %s",
            e
        )


# ============================================================
# EDIT SOURCE MESSAGE
# ============================================================

@client.on(
    events.MessageEdited(
        chats=SOURCE_CHAT_ID
    )
)
async def edited_message_handler(event):

    source_msg_id = event.message.id

    mappings = get_mappings(
        source_msg_id
    )

    if not mappings:
        return

    logging.info(
        "✏️ Editing source message %s",
        source_msg_id
    )

    for target_chat_id, target_msg_id in mappings:

        try:

            await client.edit_message(
                target_chat_id,
                target_msg_id,
                event.message
            )

            logging.info(
                "✅ Edited %s:%s",
                target_chat_id,
                target_msg_id
            )

        except Exception as e:

            logging.warning(
                "⚠️ Edit failed %s:%s -> %s",
                target_chat_id,
                target_msg_id,
                e
            )


# ============================================================
# DELETE SOURCE MESSAGE
# ============================================================

@client.on(
    events.MessageDeleted(
        chats=SOURCE_CHAT_ID
    )
)
async def deleted_message_handler(event):

    for source_msg_id in event.deleted_ids:

        mappings = get_mappings(
            source_msg_id
        )

        if not mappings:
            continue

        logging.info(
            "🗑 Deleting source message %s",
            source_msg_id
        )

        for target_chat_id, target_msg_id in mappings:

            try:

                await client.delete_messages(
                    target_chat_id,
                    [target_msg_id]
                )

                logging.info(
                    "✅ Deleted %s:%s",
                    target_chat_id,
                    target_msg_id
                )

            except Exception as e:

                logging.warning(
                    "⚠️ Delete failed %s:%s -> %s",
                    target_chat_id,
                    target_msg_id,
                    e
                )

        delete_mapping(
            source_msg_id
        )


# ============================================================
# /DEL
#
# Supports:
#
# /del
# /del@Merchantdz_bot
# /del 12345
# /del@Merchantdz_bot 12345
#
# OR reply to a published message with:
#
# /del
# ============================================================

@client.on(
    events.NewMessage(
        pattern=r"^/del(?:@\w+)?(?:\s+(\d+))?$"
    )
)
async def delete_command(event):

    sender_id = event.sender_id

    # Only admins
    if sender_id not in ADMIN_IDS:

        await event.reply(
            "❌ ما عندكش صلاحية استعمال /del."
        )

        return

    target_chat_id = event.chat_id

    if not target_chat_id:
        await event.reply(
            "❌ لا يمكن تحديد المجموعة."
        )
        return

    source_msg_id = None

    # ========================================================
    # CASE 1: /del 12345
    # ========================================================

    if event.pattern_match.group(1):

        try:
            source_msg_id = int(
                event.pattern_match.group(1)
            )
        except Exception:
            source_msg_id = None

    # ========================================================
    # CASE 2: REPLY /del
    # ========================================================

    elif event.is_reply:

        try:

            replied = await event.get_reply_message()

            if replied:

                source_msg_id = get_source_by_target(
                    target_chat_id,
                    replied.id
                )

                # If the replied message itself is source
                if source_msg_id is None:

                    source_msg_id = replied.id

        except Exception as e:

            logging.warning(
                "⚠️ Could not resolve /del reply: %s",
                e
            )

    # ========================================================
    # NOTHING FOUND
    # ========================================================

    if source_msg_id is None:

        await event.reply(
            "❌ استعمل:\n"
            "/del مع Reply على المنشور\n"
            "أو:\n"
            "/del MESSAGE_ID"
        )

        return

    # ========================================================
    # GET ALL TARGET COPIES
    # ========================================================

    mappings = get_mappings(
        source_msg_id
    )

    if not mappings:

        # Try direct delete in current chat
        try:

            await client.delete_messages(
                target_chat_id,
                [source_msg_id]
            )

            await event.reply(
                f"✅ تم حذف الرسالة {source_msg_id}."
            )

        except Exception:

            await event.reply(
                "❌ لم أجد الرسالة في قاعدة البيانات."
            )

        return

    deleted_count = 0

    # ========================================================
    # DELETE ALL COPIES
    # ========================================================

    for mapped_chat_id, mapped_msg_id in mappings:

        try:

            await client.delete_messages(
                mapped_chat_id,
                [mapped_msg_id]
            )

            deleted_count += 1

            logging.info(
                "🗑 /del deleted %s:%s",
                mapped_chat_id,
                mapped_msg_id
            )

        except Exception as e:

            logging.warning(
                "⚠️ /del failed %s:%s -> %s",
                mapped_chat_id,
                mapped_msg_id,
                e
            )

    delete_mapping(
        source_msg_id
    )

    try:

        await event.reply(
            f"✅ تم حذف المنشور من {deleted_count} قناة/مجموعة."
        )

    except Exception:
        pass


# ============================================================
# /ID
# ============================================================

@client.on(
    events.NewMessage(
        pattern=r"^/id(?:@\w+)?$"
    )
)
async def id_command(event):

    await event.reply(
        f"🆔 Chat ID:\n`{event.chat_id}`"
    )


# ============================================================
# /STATUS
# ============================================================

@client.on(
    events.NewMessage(
        pattern=r"^/status(?:@\w+)?$"
    )
)
async def status_command(event):

    if event.sender_id not in ADMIN_IDS:
        return

    cursor = db.execute(
        """
        SELECT COUNT(*)
        FROM published_messages
        """
    )

    count = cursor.fetchone()[0]

    await event.reply(
        "🟢 LEX AUTO PUBLISHER PRO\n\n"
        f"📌 Source: `{SOURCE_CHAT_ID}`\n"
        f"📤 Regular targets: `{len(TARGET_CHAT_IDS)}`\n"
        f"👮 Admins: `{len(ADMIN_IDS)}`\n"
        f"🔗 Personal channels: `{len(PERSONAL_CHANNELS)}`\n"
        f"🚫 Block rules: `{len(USER_BLOCKED_TARGETS)}`\n"
        f"💾 DB mappings: `{count}`"
    )


# ============================================================
# /HELP
# ============================================================

@client.on(
    events.NewMessage(
        pattern=r"^/help(?:@\w+)?$"
    )
)
async def help_command(event):

    await event.reply(
        "🤖 LEX AUTO PUBLISHER PRO\n\n"
        "/id — عرض ID المجموعة\n"
        "/status — حالة البوت\n"
        "/del — حذف المنشور بالـ Reply\n"
        "/del MESSAGE_ID — حذف باستعمال ID\n"
        "/help — المساعدة"
    )


# ============================================================
# START
# ============================================================

async def main():

    logging.info("🚀 Starting LEX AUTO PUBLISHER PRO...")

    await client.start(
        bot_token=BOT_TOKEN
    )

    me = await client.get_me()

    logging.info(
        "✅ Bot connected: @%s",
        getattr(me, "username", "unknown")
    )

    logging.info(
        "📥 Source: %s",
        SOURCE_CHAT_ID
    )

    logging.info(
        "📤 Targets: %s",
        TARGET_CHAT_IDS
    )

    logging.info(
        "👮 Admins: %s",
        ADMIN_IDS
    )

    logging.info(
        "🔐 Personal channels: %s",
        PERSONAL_CHANNELS
    )

    logging.info(
        "🚫 Blocked targets: %s",
        USER_BLOCKED_TARGETS
    )

    logging.info(
        "🟢 BOT 2 IS RUNNING"
    )

    await client.run_until_disconnected()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logging.info(
            "🛑 Bot stopped."
        )

    except Exception as e:

        logging.exception(
            "❌ Fatal error: %s",
            e
        ) 
