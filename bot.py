import os
import sqlite3
import logging
import asyncio
from typing import Optional

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)


def get_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(f"Missing required Variable: {name}")

    return value


def parse_int(value: str, name: str) -> int:
    try:
        return int(value.strip())
    except Exception:
        raise RuntimeError(
            f"Variable {name} must be a valid integer."
        )


def parse_int_list(value: str) -> set[int]:
    result = set()

    for item in value.split(","):
        item = item.strip()

        if not item:
            continue

        try:
            result.add(int(item))
        except Exception:
            logging.warning(
                "Invalid integer ignored: %s",
                item
            )

    return result


API_ID = parse_int(
    get_required_env("API_ID"),
    "API_ID"
)

API_HASH = get_required_env("API_HASH")

BOT_TOKEN = get_required_env("BOT_TOKEN")

OWNER_ID = parse_int(
    get_required_env("OWNER_ID"),
    "OWNER_ID"
)

SOURCE_CHAT_ID = parse_int(
    get_required_env("SOURCE_CHAT_ID"),
    "SOURCE_CHAT_ID"
)

DB_FILE = os.getenv(
    "DB_FILE",
    "lex_publisher_2.db"
).strip()


# ============================================================
# USER ACCOUNT SETTINGS
# ============================================================

USER_PHONE = os.getenv(
    "USER_PHONE",
    ""
).strip()

USER_SESSION = os.getenv(
    "USER_SESSION",
    ""
).strip()


ADMIN_IDS = parse_int_list(
    os.getenv("ADMIN_IDS", "")
)

ADMIN_IDS.add(OWNER_ID)


TARGET_CHAT_IDS = list(
    parse_int_list(
        os.getenv("TARGET_CHAT_IDS", "")
    )
)

if not TARGET_CHAT_IDS:
    raise RuntimeError(
        "TARGET_CHAT_IDS is empty."
    )


def parse_personal_channels(value: str):

    result = {}

    if not value:
        return result

    for item in value.split(";"):

        item = item.strip()

        if not item or ":" not in item:
            continue

        user_part, channel_part = item.split(":", 1)

        try:
            user_id = int(user_part.strip())
            channel_id = int(channel_part.strip())

            result[user_id] = channel_id

        except Exception:
            logging.warning(
                "Invalid PERSONAL_CHANNELS entry: %s",
                item
            )

    return result


PERSONAL_CHANNELS = parse_personal_channels(
    os.getenv("PERSONAL_CHANNELS", "")
)


def parse_user_blocked_targets(value: str):

    result = {}

    if not value:
        return result

    for item in value.split(";"):

        item = item.strip()

        if not item or ":" not in item:
            continue

        user_part, targets_part = item.split(":", 1)

        try:
            user_id = int(user_part.strip())
        except Exception:
            logging.warning(
                "Invalid blocked user: %s",
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
            except Exception:
                logging.warning(
                    "Invalid blocked target: %s",
                    target
                )

        if blocked_targets:
            result[user_id] = blocked_targets

    return result


USER_BLOCKED_TARGETS = parse_user_blocked_targets(
    os.getenv("USER_BLOCKED_TARGETS", "")
)


# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(
    DB_FILE,
    check_same_thread=False
)

db.execute(
    """
    CREATE TABLE IF NOT EXISTS published_messages (
        source_msg_id INTEGER NOT NULL,
        target_chat_id INTEGER NOT NULL,
        target_msg_id INTEGER NOT NULL,
        PRIMARY KEY (
            source_msg_id,
            target_chat_id
        )
    )
    """
)

db.commit()

db_lock = asyncio.Lock()


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


async def get_mappings(source_msg_id: int):

    async with db_lock:

        cursor = db.execute(
            """
            SELECT target_chat_id, target_msg_id
            FROM published_messages
            WHERE source_msg_id = ?
            """,
            (source_msg_id,)
        )

        return cursor.fetchall()


async def delete_all_mappings(source_msg_id: int):

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
# TARGETS
# ============================================================

def get_targets_for_user(sender_id: Optional[int]):

    targets = list(TARGET_CHAT_IDS)

    if sender_id is not None:

        personal_channel = PERSONAL_CHANNELS.get(
            sender_id
        )

        if personal_channel:
            targets.append(personal_channel)

    targets = list(
        dict.fromkeys(targets)
    )

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
# BOT CLIENT
# ============================================================

bot_client = TelegramClient(
    "lex_publisher_bot_2",
    API_ID,
    API_HASH
)


# ============================================================
# USER CLIENT
# ============================================================

if USER_SESSION:

    from telethon.sessions import StringSession

    user_client = TelegramClient(
        StringSession(USER_SESSION),
        API_ID,
        API_HASH
    )

else:

    user_client = TelegramClient(
        "lex_publisher_user_2",
        API_ID,
        API_HASH
    )


# ============================================================
# SEND SYSTEM
# ============================================================

async def send_with_user_as(
    target_chat_id: int,
    message,
    reply_to: Optional[int] = None
):

    """
    First try sending as the target chat/channel
    using the user account.

    If Telegram refuses Send As, fallback to BOT client.
    """

    # --------------------------------------------------------
    # TRY USER ACCOUNT + SEND AS TARGET
    # --------------------------------------------------------

    try:

        sent = await user_client.send_message(
            entity=target_chat_id,
            message=message,
            reply_to=reply_to,
            send_as=target_chat_id
        )

        logging.info(
            "SEND AS SUCCESS: %s",
            target_chat_id
        )

        return sent

    except FloodWaitError as e:

        logging.warning(
            "User FloodWait %s seconds for %s",
            e.seconds,
            target_chat_id
        )

        await asyncio.sleep(e.seconds)

        try:

            sent = await user_client.send_message(
                entity=target_chat_id,
                message=message,
                reply_to=reply_to,
                send_as=target_chat_id
            )

            return sent

        except Exception as retry_error:

            logging.warning(
                "User Send As retry failed for %s: %s",
                target_chat_id,
                retry_error
            )

    except Exception as e:

        logging.warning(
            "Send As unavailable for %s: %s",
            target_chat_id,
            e
        )

    # --------------------------------------------------------
    # FALLBACK TO BOT
    # --------------------------------------------------------

    try:

        sent = await bot_client.send_message(
            entity=target_chat_id,
            message=message,
            reply_to=reply_to
        )

        logging.info(
            "FALLBACK BOT SEND: %s",
            target_chat_id
        )

        return sent

    except FloodWaitError as e:

        logging.warning(
            "Bot FloodWait %s seconds for %s",
            e.seconds,
            target_chat_id
        )

        await asyncio.sleep(e.seconds)

        try:

            return await bot_client.send_message(
                entity=target_chat_id,
                message=message,
                reply_to=reply_to
            )

        except Exception as retry_error:

            logging.error(
                "Bot retry failed for %s: %s",
                target_chat_id,
                retry_error
            )

    except Exception as e:

        logging.error(
            "Bot fallback failed for %s: %s",
            target_chat_id,
            e
        )

    return None


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
# PUBLISH
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
        return

    reply_source_id = None

    try:

        if message.is_reply:

            reply_msg = await message.get_reply_message()

            if reply_msg:
                reply_source_id = reply_msg.id

    except Exception as e:

        logging.warning(
            "Reply detection failed: %s",
            e
        )

    for target_chat_id in targets:

        try:

            reply_to = None

            if reply_source_id:

                reply_to = await find_reply_target(
                    reply_source_id,
                    target_chat_id
                )

            sent = await send_with_user_as(
                target_chat_id,
                message,
                reply_to
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

@bot_client.on(
    events.NewMessage(
        chats=SOURCE_CHAT_ID
    )
)
async def source_new_message(event):

    try:

        message = event.message

        if not message:
            return

        if message.text:

            first_word = (
                message.text
                .strip()
                .split()[0]
                .lower()
            )

            if first_word.startswith(
                (
                    "/del",
                    "/status",
                    "/id",
                    "/help"
                )
            ):
                return

        await publish_message(
            message,
            sender_id=event.sender_id
        )

    except Exception:

        logging.exception(
            "Source new message error"
        )


# ============================================================
# EDIT
# ============================================================

@bot_client.on(
    events.MessageEdited(
        chats=SOURCE_CHAT_ID
    )
)
async def source_message_edited(event):

    try:

        message = event.message

        if not message:
            return

        if message.text:

            first_word = (
                message.text
                .strip()
                .split()[0]
                .lower()
            )

            if first_word.startswith(
                (
                    "/del",
                    "/status",
                    "/id",
                    "/help"
                )
            ):
                return

        mappings = await get_mappings(
            message.id
        )

        if not mappings:
            return

        for target_chat_id, target_msg_id in mappings:

            try:

                await bot_client.edit_message(
                    entity=target_chat_id,
                    message=target_msg_id,
                    text=message
                )

            except Exception as e:

                logging.error(
                    "Edit failed %s/%s: %s",
                    target_chat_id,
                    target_msg_id,
                    e
                )

    except Exception:

        logging.exception(
            "Edited message error"
        )


# ============================================================
# DELETE SOURCE MESSAGE
# ============================================================

@bot_client.on(
    events.MessageDeleted(
        chats=SOURCE_CHAT_ID
    )
)
async def source_message_deleted(event):

    try:

        for source_msg_id in event.deleted_ids:

            mappings = await get_mappings(
                source_msg_id
            )

            for target_chat_id, target_msg_id in mappings:

                try:

                    await bot_client.delete_messages(
                        entity=target_chat_id,
                        message_ids=[target_msg_id]
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

    except Exception:

        logging.exception(
            "Deleted message handler error"
        )


# ============================================================
# /id
# ============================================================

@bot_client.on(
    events.NewMessage(
        pattern=r"^/id(?:@\w+)?$"
    )
)
async def command_id(event):

    if event.chat_id != SOURCE_CHAT_ID:
        return

    if event.sender_id not in ADMIN_IDS:
        return

    await event.reply(
        f"Chat ID: `{event.chat_id}`\n"
        f"User ID: `{event.sender_id}`"
    )


# ============================================================
# /status
# ============================================================

@bot_client.on(
    events.NewMessage(
        pattern=r"^/status(?:@\w+)?$"
    )
)
async def command_status(event):

    if event.chat_id != SOURCE_CHAT_ID:
        return

    if event.sender_id not in ADMIN_IDS:
        return

    sender_id = event.sender_id

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
        "LEX AUTO PUBLISHER PRO\n\n"
        f"User: {sender_id}\n"
        "Admin: True\n"
        f"Allowed targets: {len(targets)}\n"
        f"Blocked targets: {len(blocked)}\n"
    )

    if personal:

        text += (
            f"Personal channel: {personal}\n"
        )

    await event.reply(text)


# ============================================================
# /del
# ============================================================

@bot_client.on(
    events.NewMessage(
        pattern=r"^/del(?:@\w+)?(?:\s+(\d+))?$"
    )
)
async def command_delete(event):

    if event.chat_id != SOURCE_CHAT_ID:
        return

    if event.sender_id not in ADMIN_IDS:
        return

    source_msg_id = None

    try:

        message_id_text = (
            event.pattern_match.group(1)
        )

    except Exception:

        message_id_text = None

    if message_id_text:

        try:

            source_msg_id = int(
                message_id_text
            )

        except Exception:

            source_msg_id = None

    if source_msg_id is None:

        try:

            if event.is_reply:

                replied = await event.get_reply_message()

                if replied:
                    source_msg_id = replied.id

        except Exception as e:

            logging.error(
                "Reply detection error: %s",
                e
            )

    if source_msg_id is None:

        try:

            await bot_client.delete_messages(
                entity=SOURCE_CHAT_ID,
                message_ids=[event.id]
            )

        except Exception:

            pass

        return

    mappings = await get_mappings(
        source_msg_id
    )

    for target_chat_id, target_msg_id in mappings:

        try:

            await bot_client.delete_messages(
                entity=target_chat_id,
                message_ids=[target_msg_id]
            )

            logging.info(
                "Deleted target copy %s/%s",
                target_chat_id,
                target_msg_id
            )

        except Exception as e:

            logging.error(
                "Target delete failed %s/%s: %s",
                target_chat_id,
                target_msg_id,
                e
            )

    await delete_all_mappings(
        source_msg_id
    )

    try:

        await bot_client.delete_messages(
            entity=SOURCE_CHAT_ID,
            message_ids=[source_msg_id]
        )

        logging.info(
            "Deleted original post %s",
            source_msg_id
        )

    except Exception as e:

        logging.error(
            "Could not delete original post: %s",
            e
        )

    try:

        await bot_client.delete_messages(
            entity=SOURCE_CHAT_ID,
            message_ids=[event.id]
        )

        logging.info(
            "Deleted /del command: %s",
            event.id
        )

    except Exception as e:

        logging.error(
            "Could not delete /del command: %s",
            e
        )


# ============================================================
# /help
# ============================================================

@bot_client.on(
    events.NewMessage(
        pattern=r"^/help(?:@\w+)?$"
    )
)
async def command_help(event):

    if event.chat_id != SOURCE_CHAT_ID:
        return

    if event.sender_id not in ADMIN_IDS:
        return

    await event.reply(
        "LEX AUTO PUBLISHER PRO\n\n"
        "/id\n"
        "/status\n"
        "/del\n"
        "/del 123456\n\n"
        "Reply to a post and send /del to delete it everywhere."
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
        "Targets: %s",
        TARGET_CHAT_IDS
    )

    logging.info(
        "Admins: %s",
        sorted(ADMIN_IDS)
    )

    logging.info(
        "Personal channels: %s",
        PERSONAL_CHANNELS
    )

    logging.info(
        "Blocked targets: %s",
        USER_BLOCKED_TARGETS
    )

    logging.info(
        "Database: %s",
        DB_FILE
    )

    logging.info(
        "Send As system: ENABLED"
    )

    logging.info(
        "======================================"
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    # Start BOT
    await bot_client.start(
        bot_token=BOT_TOKEN
    )

    # Start USER ACCOUNT
    if USER_SESSION:

        await user_client.start()

    else:

        if not USER_PHONE:

            raise RuntimeError(
                "USER_PHONE is required when USER_SESSION is empty."
            )

        await user_client.start(
            phone=USER_PHONE
        )

    await startup()

    logging.info(
        "Bot + User Session are running..."
    )

    await asyncio.gather(
        bot_client.run_until_disconnected(),
        user_client.run_until_disconnected()
    )


if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        logging.info(
            "System stopped."
        )

    except Exception:

        logging.exception(
            "Fatal error"
    ) 
