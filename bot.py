import os
import re
import sqlite3
import logging
import asyncio
from typing import Optional

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError

load_dotenv()

# ============================================================
# LEX AUTO PUBLISHER PRO - BOT 2
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# ============================================================
# ENV HELPERS
# ============================================================

def get_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(f"Missing required Variable: {name}")

    return value


def parse_int(value: str, name: str) -> int:
    try:
        return int(value.strip())
    except ValueError:
        raise RuntimeError(
            f"Variable {name} must be a valid integer."
        )


def parse_int_list(value: str) -> set[int]:
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
                "Invalid integer ignored: %s",
                item
            )

    return result


# ============================================================
# BASIC VARIABLES
# ============================================================

API_ID = parse_int(
    get_required_env("API_ID"),
    "API_ID"
)

API_HASH = get_required_env("API_HASH")

BOT_TOKEN = get_required_env("BOT_TOKEN")

DB_FILE = os.getenv(
    "DB_FILE",
    "lex_publisher_2.db"
).strip()

OWNER_ID = parse_int(
    get_required_env("OWNER_ID"),
    "OWNER_ID"
)

SOURCE_CHAT_ID = parse_int(
    get_required_env("SOURCE_CHAT_ID"),
    "SOURCE_CHAT_ID"
)

# ============================================================
# ADMIN IDS
# Supports:
# 123,456,789
# ============================================================

ADMIN_IDS = parse_int_list(
    os.getenv("ADMIN_IDS", "")
)

# Owner is always admin
ADMIN_IDS.add(OWNER_ID)

# ============================================================
# NORMAL TARGET CHANNELS
# ============================================================

TARGET_CHAT_IDS = list(
    parse_int_list(
        os.getenv("TARGET_CHAT_IDS", "")
    )
)

if not TARGET_CHAT_IDS:
    raise RuntimeError(
        "TARGET_CHAT_IDS is empty."
    )

# ============================================================
# USER BLOCKED TARGETS
#
# Format:
#
# USER_BLOCKED_TARGETS=
# user1:target1,target2;
# user2:target3;
# user3:target4
# ============================================================

def parse_user_blocked_targets(value: str):
    result = {}

    if not value:
        return result

    for item in value.split(";"):

        item = item.strip()

        if not item or ":" not in item:
            continue

        user_part, targets_part = item.split(
            ":",
            1
        )

        try:
            user_id = int(
                user_part.strip()
            )
        except ValueError:
            logging.warning(
                "Invalid blocked user ID: %s",
                user_part
            )
            continue

        blocked_targets = set()

        for target in targets_part.split(","):

            target = target.strip()

            if not target:
                continue

            try:
                blocked_targets.add(
                    int(target)
                )
            except ValueError:
                logging.warning(
                    "Invalid blocked target: %s",
                    target
                )

        if blocked_targets:
            result[user_id] = blocked_targets

    return result


USER_BLOCKED_TARGETS = parse_user_blocked_targets(
    os.getenv(
        "USER_BLOCKED_TARGETS",
        ""
    )
)

# ============================================================
# PERSONAL CHANNELS
#
# Format:
#
# user_id:channel_id;
# user_id:channel_id
# ============================================================

def parse_personal_channels(value: str):
    result = {}

    if not value:
        return result

    for item in value.split(";"):

        item = item.strip()

        if not item or ":" not in item:
            continue

        user_part, channel_part = item.split(
            ":",
            1
        )

        try:
            user_id = int(
                user_part.strip()
            )

            channel_id = int(
                channel_part.strip()
            )

        except ValueError:
            logging.warning(
                "Invalid PERSONAL_CHANNELS entry: %s",
                item
            )
            continue

        result[user_id] = channel_id

    return result


PERSONAL_CHANNELS = parse_personal_channels(
    os.getenv(
        "PERSONAL_CHANNELS",
        ""
    )
)

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
    PRIMARY KEY (
        source_msg_id,
        target_chat_id
    )
)
""")

db.commit()

db_lock = asyncio.Lock()


# ============================================================
# DATABASE FUNCTIONS
# ============================================================

async def save_mapping(
    source_msg_id: int,
    target_chat_id: int,
    target_msg_id: int
):
    async with db_lock:

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


async def get_mappings(
    source_msg_id: int
):
    async with db_lock:

        cursor = db.execute(
            """
            SELECT
                target_chat_id,
                target_msg_id
            FROM published_messages
            WHERE source_msg_id = ?
            """,
            (source_msg_id,)
        )

        return cursor.fetchall()


async def delete_mapping(
    source_msg_id: int,
    target_chat_id: int
):
    async with db_lock:

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


async def delete_all_mappings(
    source_msg_id: int
):
    async with db_lock:

        db.execute(
            """
            DELETE FROM published_messages
            WHERE source_msg_id = ?
            """,
            (source_msg_id,)
        )

        db.commit()


# ============================================================
# TARGET MANAGEMENT
# ============================================================

def get_all_possible_targets():

    targets = set(TARGET_CHAT_IDS)

    targets.update(
        PERSONAL_CHANNELS.values()
    )

    return list(targets)


def get_targets_for_user(
    sender_id: Optional[int]
):

    targets = list(
        TARGET_CHAT_IDS
    )

    # Add personal channel ONLY for its owner
    if sender_id is not None:

        personal_channel = PERSONAL_CHANNELS.get(
            sender_id
        )

        if personal_channel:
            targets.append(
                personal_channel
            )

    # Remove duplicates
    targets = list(
        dict.fromkeys(targets)
    )

    # Apply user-specific blocks
    blocked = USER_BLOCKED_TARGETS.get(
        sender_id,
        set()
    )

    targets = [
        target
        for target in targets
        if target not in blocked
    ]

    return targets


# ============================================================
# TELEGRAM CLIENT
# ============================================================

client = TelegramClient(
    "lex_publisher_bot_2",
    API_ID,
    API_HASH
)


# ============================================================
# SEND MESSAGE
# ============================================================

async def send_to_target(
    target_chat_id: int,
    message,
    reply_to: Optional[int] = None
):

    try:

        sent = await client.send_message(
            entity=target_chat_id,
            message=message,
            reply_to=reply_to
        )

        return sent

    except FloodWaitError as e:

        logging.warning(
            "FloodWait %s seconds for %s",
            e.seconds,
            target_chat_id
        )

        await asyncio.sleep(
            e.seconds
        )

        try:

            sent = await client.send_message(
                entity=target_chat_id,
                message=message,
                reply_to=reply_to
            )

            return sent

        except Exception as retry_error:

            logging.error(
                "Retry failed for %s: %s",
                target_chat_id,
                retry_error
            )

    except Exception as e:

        logging.error(
            "Send failed to %s: %s",
            target_chat_id,
            e
        )

    return None


# ============================================================
# GET TARGET REPLY MESSAGE
# ============================================================

async def find_reply_target(
    source_reply_msg_id: int,
    target_chat_id: int
):

    mappings = await get_mappings(
        source_reply_msg_id
    )

    for chat_id, target_msg_id in mappings:

        if int(chat_id) == int(target_chat_id):

            return target_msg_id

    return None


# ============================================================
# PUBLISH MESSAGE
# ============================================================

async def publish_message(
    message,
    sender_id: Optional[int] = None
):

    if not message:
        return

    source_msg_id = message.id

    targets = get_targets_for_user(
        sender_id
    )

    if not targets:
        logging.info(
            "No allowed targets for user %s",
            sender_id
        )
        return

    # --------------------------------------------------------
    # Detect reply
    # --------------------------------------------------------

    reply_source_id = None

    try:

        if message.is_reply:

            reply_msg = await message.get_reply_message()

            if reply_msg:

                reply_source_id = reply_msg.id

    except Exception as e:

        logging.warning(
            "Could not detect reply: %s",
            e
        )

    # --------------------------------------------------------
    # Send to every allowed target
    # --------------------------------------------------------

    for target_chat_id in targets:

        try:

            reply_to = None

            if reply_source_id:

                reply_to = await find_reply_target(
                    reply_source_id,
                    target_chat_id
                )

            sent = await send_to_target(
                target_chat_id=target_chat_id,
                message=message,
                reply_to=reply_to
            )

            if sent:

                await save_mapping(
                    source_msg_id,
                    target_chat_id,
                    sent.id
                )

                logging.info(
                    "Published %s -> %s (%s)",
                    source_msg_id,
                    target_chat_id,
                    sent.id
                )

        except Exception as e:

            logging.error(
                "Publish error %s -> %s: %s",
                source_msg_id,
                target_chat_id,
                e
            )


# ============================================================
# SOURCE NEW MESSAGE
# ============================================================

@client.on(
    events.NewMessage(
        chats=SOURCE_CHAT_ID
    )
)
async def source_new_message(
    event
):

    try:

        message = event.message

        # Ignore service messages
        if not message:
            return

        sender_id = event.sender_id

        await publish_message(
            message,
            sender_id=sender_id
        )

    except Exception as e:

        logging.exception(
            "Source new message error: %s",
            e
        )


# ============================================================
# EDITED SOURCE MESSAGE
# ============================================================

@client.on(
    events.MessageEdited(
        chats=SOURCE_CHAT_ID
    )
)
async def source_message_edited(
    event
):

    try:

        source_msg_id = event.message.id

        mappings = await get_mappings(
            source_msg_id
        )

        if not mappings:
            return

        for target_chat_id, target_msg_id in mappings:

            try:

                await client.edit_message(
                    entity=target_chat_id,
                    message=target_msg_id,
                    text=event.message
                )

                logging.info(
                    "Edited %s -> %s/%s",
                    source_msg_id,
                    target_chat_id,
                    target_msg_id
                )

            except Exception as e:

                logging.error(
                    "Edit failed %s/%s: %s",
                    target_chat_id,
                    target_msg_id,
                    e
                )

    except Exception as e:

        logging.exception(
            "Edited message error: %s",
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
async def source_message_deleted(
    event
):

    try:

        for source_msg_id in event.deleted_ids:

            mappings = await get_mappings(
                source_msg_id
            )

            for target_chat_id, target_msg_id in mappings:

                try:

                    await client.delete_messages(
                        entity=target_chat_id,
                        message_ids=[target_msg_id]
                    )

                    logging.info(
                        "Deleted %s from %s",
                        target_msg_id,
                        target_chat_id
                    )

                except Exception as e:

                    logging.error(
                        "Delete failed %s/%s: %s",
                        target_chat_id,
                        target_msg_id,
                        e
                    )

            await delete_all_mappings(
                source_msg_id
            )

    except Exception as e:

        logging.exception(
            "Deleted message handler error: %s",
            e
        )


# ============================================================
# /ID
# ============================================================

@client.on(
    events.NewMessage(
        pattern=r"^/id(?:@\w+)?$"
    )
)
async def command_id(
    event
):

    await event.reply(
        f"Chat ID: `{event.chat_id}`\n"
        f"User ID: `{event.sender_id}`"
    )


# ============================================================
# /STATUS
# ============================================================

@client.on(
    events.NewMessage(
        pattern=r"^/status(?:@\w+)?$"
    )
)
async def command_status(
    event
):

    sender_id = event.sender_id

    is_admin = (
        sender_id in ADMIN_IDS
    )

    targets = get_targets_for_user(
        sender_id
    )

    blocked = USER_BLOCKED_TARGETS.get(
        sender_id,
        set()
    )

    personal = PERSONAL_CHANNELS.get(
        sender_id
    )

    text = (
        "🤖 LEX AUTO PUBLISHER PRO\n\n"
        f"👤 User: `{sender_id}`\n"
        f"👑 Admin: `{is_admin}`\n"
        f"📡 Allowed targets: `{len(targets)}`\n"
        f"🚫 Blocked targets: `{len(blocked)}`\n"
    )

    if personal:
        text += (
            f"📢 Personal channel: `{personal}`\n"
        )

    await event.reply(
        text
    )


# ============================================================
# /DEL
#
# Supported:
#
# /del
# /del@BotName
# /del 123456
# /del@BotName 123456
#
# Also works as reply:
#
# Reply to a published message + /del
# ============================================================

@client.on(
    events.NewMessage(
        pattern=r"^/del(?:@\w+)?(?:\s+(\d+))?$"
    )
)
async def command_delete(
    event
):

    sender_id = event.sender_id

    # --------------------------------------------------------
    # Permission
    # --------------------------------------------------------

    if sender_id not in ADMIN_IDS:

        await event.reply(
            "❌ You don't have permission."
        )

        return

    match = event.pattern_match

    message_id_text = None

    if match:

        try:
            message_id_text = match.group(1)
        except Exception:
            message_id_text = None

    source_msg_id = None

    # --------------------------------------------------------
    # /del 123456
    # --------------------------------------------------------

    if message_id_text:

        try:

            source_msg_id = int(
                message_id_text
            )

        except ValueError:

            await event.reply(
                "❌ Invalid message ID."
            )

            return

    # --------------------------------------------------------
    # /del as reply
    # --------------------------------------------------------

    if source_msg_id is None:

        try:

            if event.is_reply:

                replied = await event.get_reply_message()

                if replied:

                    # If this is a copied target message,
                    # find source message from DB.
                    async with db_lock:

                        cursor = db.execute(
                            """
                            SELECT source_msg_id
                            FROM published_messages
                            WHERE target_chat_id = ?
                            AND target_msg_id = ?
                            LIMIT 1
                            """,
                            (
                                event.chat_id,
                                replied.id
                            )
                        )

                        row = cursor.fetchone()

                    if row:

                        source_msg_id = int(
                            row[0]
                        )

                    else:

                        # If command is in source chat,
                        # directly use replied message ID.
                        if event.chat_id == SOURCE_CHAT_ID:

                            source_msg_id = replied.id

        except Exception as e:

            logging.error(
                "Reply detection error: %s",
                e
            )

    # --------------------------------------------------------
    # Nothing found
    # --------------------------------------------------------

    if source_msg_id is None:

        await event.reply(
            "❌ Reply to a published message "
            "or use:\n"
            "`/del MESSAGE_ID`"
        )

        return

    # --------------------------------------------------------
    # Find all copies
    # --------------------------------------------------------

    mappings = await get_mappings(
        source_msg_id
    )

    deleted_count = 0

    for target_chat_id, target_msg_id in mappings:

        try:

            await client.delete_messages(
                entity=target_chat_id,
                message_ids=[target_msg_id]
            )

            deleted_count += 1

        except Exception as e:

            logging.error(
                "Delete copy failed %s/%s: %s",
                target_chat_id,
                target_msg_id,
                e
            )

    # --------------------------------------------------------
    # Delete DB mapping
    # --------------------------------------------------------

    await delete_all_mappings(
        source_msg_id
    )

    await event.reply(
        "✅ Deleted successfully.\n\n"
        f"🆔 Source: `{source_msg_id}`\n"
        f"🗑 Copies deleted: `{deleted_count}`"
    )


# ============================================================
# /HELP
# ============================================================

@client.on(
    events.NewMessage(
        pattern=r"^/help(?:@\w+)?$"
    )
)
async def command_help(
    event
):

    await event.reply(
        "🤖 LEX AUTO PUBLISHER PRO\n\n"
        "Commands:\n\n"
        "🆔 `/id`\n"
        "📊 `/status`\n"
        "🗑 `/del`\n"
        "🗑 `/del 123456`\n\n"
        "يمكن استعمال `/del` كرد على المنشور."
    )


# ============================================================
# STARTUP
# ============================================================

async def startup():

    logging.info(
        "======================================"
    )

    logging.info(
        "LEX AUTO PUBLISHER PRO - BOT 2"
    )

    logging.info(
        "Source: %s",
        SOURCE_CHAT_ID
    )

    logging.info(
        "Normal targets: %s",
        TARGET_CHAT_IDS
    )

    logging.info(
        "Admins: %s",
        sorted(ADMIN_IDS)
    )

    logging.info(
        "Blocked users: %s",
        len(USER_BLOCKED_TARGETS)
    )

    logging.info(
        "Personal channels: %s",
        len(PERSONAL_CHANNELS)
    )

    logging.info(
        "Database: %s",
        DB_FILE
    )

    logging.info(
        "======================================"
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    await client.start(
        bot_token=BOT_TOKEN
    )

    await startup()

    logging.info(
        "Bot is running..."
    )

    await client.run_until_disconnected()


if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logging.info(
            "Bot stopped."
        )

    except Exception as e:

        logging.exception(
            "Fatal error: %s",
            e
) 
