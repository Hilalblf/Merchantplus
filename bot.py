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
            logger.warning("FloodWait: %s seconds", e.seconds)
            await asyncio.sleep(e.seconds + 1)
        except RPCError as e:
            logger.error("Telegram RPC error: %s", e)
            if attempt >= 2:
                return None
            await asyncio.sleep(2)
        except Exception as e:
            logger.exception("Operation error: %s", e)
            if attempt >= 2:
                return None
            await asyncio.sleep(2)
    return None


# ============================================================
# SEND MESSAGE
# ============================================================

async def send_to_target(message, target_chat_id, reply_to=None):
    text = message.text or ""

    if not text.strip() and not message.media:
        return None

    if message.media:
        # send_file لا يقبل link_preview كباراميتر
        kwargs = {"formatting_entities": message.entities}
        if reply_to is not None:
            kwargs["reply_to"] = reply_to
            logger.info("REPLY SEND | TARGET=%s | REPLY_TO=%s", target_chat_id, reply_to)

        sent = await run_with_retry(
            client.send_file,
            target_chat_id,
            message.media,
            caption=text,
            **kwargs
        )
    else:
        kwargs = {
            "formatting_entities": message.entities,
            "link_preview": False
        }
        if reply_to is not None:
            kwargs["reply_to"] = reply_to
            logger.info("REPLY SEND | TARGET=%s | REPLY_TO=%s", target_chat_id, reply_to)

        sent = await run_with_retry(
            client.send_message,
            target_chat_id,
            text,
            **kwargs
        )

    if sent is None:
        return None

    return sent.id


# ============================================================
# PUBLISH MESSAGE
# ============================================================

async def publish_message(message, source_chat_id=SOURCE_CHAT_ID):
    logger.info("PUBLISH SOURCE MESSAGE: %s", message.id)

    parent_source_id = None
    if message.reply_to_msg_id:
        parent_source_id = message.reply_to_msg_id
        logger.info("SOURCE REPLY DETECTED | MESSAGE=%s | PARENT=%s", message.id, parent_source_id)

    parent_mappings = {}
    if parent_source_id is not None:
        rows = get_mappings(parent_source_id, source_chat_id)
        parent_mappings = {
            int(target_chat_id): int(target_message_id)
            for target_chat_id, target_message_id in rows
        }
        logger.info("PARENT MAPPINGS FOUND: %s", parent_mappings)

    success = 0

    # النشر الأساسي بيروح لـ 5 المجموعات دايمًا
    all_targets = list(TARGET_CHAT_IDS)

    # لو الرسالة من الصديق، ضيف القناة الخاصة به فقط لهذه الرسالة
    if message.sender_id == FRIEND_USER_ID:
        if FRIEND_CHANNEL_ID not in all_targets:
            all_targets.append(FRIEND_CHANNEL_ID)
            logger.info("FRIEND MESSAGE DETECTED | ADDING CHANNEL %s TO TARGETS", FRIEND_CHANNEL_ID)

    for target_chat_id in all_targets:
        target_chat_id = int(target_chat_id)
        reply_to = parent_mappings.get(target_chat_id)

        target_message_id = await send_to_target(
            message,
            target_chat_id,
            reply_to=reply_to
        )

        if target_message_id is None:
            logger.error("COPY FAILED | SOURCE=%s | TARGET=%s", message.id, target_chat_id)
            continue

        save_mapping(message.id, target_chat_id, target_message_id, source_chat_id)
        success += 1
        logger.info("COPIED | SOURCE=%s -> TARGET=%s:%s", message.id, target_chat_id, target_message_id)

        await asyncio.sleep(0.3)

    logger.info("PUBLISH COMPLETE | SOURCE=%s | %s/%s", message.id, success, len(all_targets))


# ============================================================
# EVENT HANDLERS
# ============================================================

@client.on(events.NewMessage(chats=SOURCE_CHAT_ID))
async def source_new_message(event):
    try:
        message = event.message
        if BOT_ID is not None and event.sender_id == BOT_ID:
            return

        text = message.text or ""
        if not text.strip() and not message.media:
            return
        if text.startswith("/"):
            return

        logger.info("NEW SOURCE MESSAGE | ID=%s | SENDER=%s | REPLY_TO=%s", message.id, event.sender_id, message.reply_to_msg_id)
        await publish_message(message)
    except Exception as e:
        logger.exception("NEW MESSAGE ERROR: %s", e)


@client.on(events.MessageEdited(chats=SOURCE_CHAT_ID))
async def source_edit_message(event):
    try:
        message = event.message
        text = message.text or ""
        if not text.strip() and not message.media:
            return
        if text.startswith("/"):
            return

        mappings = get_mappings(message.id)
        if not mappings:
            return

        for target_chat_id, target_message_id in mappings:
            await run_with_retry(
                client.edit_message,
                target_chat_id,
                target_message_id,
                text,
                formatting_entities=message.entities
            )
            await asyncio.sleep(0.3)
    except Exception as e:
        logger.exception("EDIT HANDLER ERROR: %s", e)


async def delete_source_message(source_message_id, source_chat_id=SOURCE_CHAT_ID):
    mappings = get_mappings(source_message_id, source_chat_id)
    if not mappings:
        return

    for target_chat_id, target_message_id in mappings:
        await run_with_retry(
            client.delete_messages,
            target_chat_id,
            [target_message_id]
        )
        await asyncio.sleep(0.3)

    delete_mappings(source_message_id, source_chat_id)


@client.on(events.MessageDeleted(chats=SOURCE_CHAT_ID))
async def source_deleted_message(event):
    try:
        for message_id in event.deleted_ids:
            await delete_source_message(message_id)
    except Exception as e:
        logger.exception("DELETE HANDLER ERROR: %s", e)


# ============================================================
# SPECIAL ADDITIONAL CHANNELS
# ============================================================

for _special_chat_id, _special_owner_id in SPECIAL_CHANNELS.items():

    @client.on(events.NewMessage(chats=_special_chat_id))
    async def special_channel_new_message(event, special_chat_id=_special_chat_id, special_owner_id=_special_owner_id):
        try:
            message = event.message
            text = message.text or ""
            if not text.strip() and not message.media:
                return
            if text.startswith("/"):
                return

            logger.info(
                "NEW SPECIAL CHANNEL MESSAGE | CHANNEL=%s | ID=%s | SENDER=%s",
                special_chat_id, message.id, event.sender_id
            )

            await publish_message(message, special_chat_id)
        except Exception as e:
            logger.exception("SPECIAL CHANNEL NEW MESSAGE ERROR: %s", e)

    @client.on(events.MessageEdited(chats=_special_chat_id))
    async def special_channel_edit_message(event, special_chat_id=_special_chat_id):
        try:
            message = event.message
            text = message.text or ""
            if not text.strip() and not message.media:
                return
            if text.startswith("/"):
                return

            mappings = get_mappings(message.id, special_chat_id)
            if not mappings:
                return

            for target_chat_id, target_message_id in mappings:
                await run_with_retry(
                    client.edit_message,
                    target_chat_id,
                    target_message_id,
                    text,
                    formatting_entities=message.entities
                )
                await asyncio.sleep(0.3)
        except Exception as e:
            logger.exception("SPECIAL EDIT HANDLER ERROR: %s", e)

    @client.on(events.MessageDeleted(chats=_special_chat_id))
    async def special_channel_deleted_message(event, special_chat_id=_special_chat_id):
        try:
            for message_id in event.deleted_ids:
                await delete_source_message(message_id, special_chat_id)
        except Exception as e:
            logger.exception("SPECIAL DELETE HANDLER ERROR: %s", e)


@client.on(events.NewMessage(chats=SOURCE_CHAT_ID, pattern=r"^/del(@\w+)?$"))
async def del_handler(event):
    if not is_allowed(event.sender_id):
        return

    if not event.is_reply:
        return

    replied = await event.get_reply_message()
    if replied is None:
        return

    source_message_id = replied.id
    mappings = get_mappings(source_message_id)
    if not mappings:
        return

    for target_chat_id, target_message_id in mappings:
        await run_with_retry(
            client.delete_messages,
            target_chat_id,
            [target_message_id]
        )
        await asyncio.sleep(0.3)

    delete_mappings(source_message_id)

    try:
        await client.delete_messages(SOURCE_CHAT_ID, [source_message_id])
    except Exception:
        pass

    try:
        await event.delete()
    except Exception:
        pass


@client.on(events.NewMessage(chats=SOURCE_CHAT_ID, pattern=r"^/status$"))
async def status_handler(event):
    if not is_allowed(event.sender_id):
        return

    await event.reply(
        f"🤖 LEX AUTO PUBLISHER PRO ({BOT_NAME})\n\n"
        "🟢 STATUS: ONLINE\n\n"
        f"🏠 SOURCE: `{SOURCE_CHAT_ID}`\n"
        f"🎯 TARGETS: {len(TARGET_CHAT_IDS)}"
    )


# ============================================================
# START
# ============================================================

async def main():
    global BOT_ID
    init_db()

    logger.info("========================================")
    logger.info("LEX AUTO PUBLISHER PRO - %s", BOT_NAME)
    logger.info("SOURCE: %s", SOURCE_CHAT_ID)
    logger.info("TARGETS: %s", TARGET_CHAT_IDS)
    logger.info("DATABASE: %s", DB_FILE)

    await client.start(bot_token=BOT_TOKEN)
    me = await client.get_me()
    BOT_ID = me.id

    logger.info("BOT ID: %s", BOT_ID)
    logger.info("USERNAME: @%s", getattr(me, "username", ""))
    logger.info("STATUS: ONLINE")
    logger.info("========================================")

    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("LEX STOPPED")
