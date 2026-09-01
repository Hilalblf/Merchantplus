import os
import asyncio
import logging
import sqlite3

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# LEX AUTO PUBLISHER PRO
# SOURCE -> 5 TARGET GROUPS
# ============================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]

# صاحب البوت + الأصدقاء المسموح لهم (IDs مفصولة بفاصلة فـ OWNER_ID)
# مثال فـ Railway Variables: OWNER_ID=822007358,111111111,222222222
ALLOWED_USER_IDS = [
    int(x.strip())
    for x in os.environ["OWNER_ID"].split(",")
    if x.strip()
]

def is_allowed(user_id):
    return user_id in ALLOWED_USER_IDS

# ============================================================
# SOURCE GROUP
# ============================================================

SOURCE_CHAT_ID = int(os.environ["SOURCE_CHAT_ID"])

# ============================================================
# TARGET GROUPS
# ============================================================

TARGET_CHAT_IDS = [
    int(x.strip())
    for x in os.environ["TARGET_CHAT_IDS"].split(",")
]

# ============================================================
# DATABASE
# ============================================================

DB_FILE = os.getenv("DB_FILE", "lex_publisher.db")

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("LEX")


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


# ============================================================
# SAVE MAPPING
# ============================================================

def save_mapping(
    source_message_id,
    target_chat_id,
    target_message_id
):

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
            SOURCE_CHAT_ID,
            source_message_id,
            target_chat_id,
            target_message_id
        ))

        conn.commit()

    finally:
        conn.close()


# ============================================================
# GET ALL TARGET MAPPINGS
# ============================================================

def get_mappings(source_message_id):

    conn = sqlite3.connect(DB_FILE, timeout=30)

    try:

        rows = conn.execute("""
            SELECT
                target_chat_id,
                target_message_id

            FROM message_map

            WHERE source_chat_id = ?
              AND source_message_id = ?

        """, (
            SOURCE_CHAT_ID,
            source_message_id
        )).fetchall()

        return rows

    finally:
        conn.close()


# ============================================================
# DELETE MAPPINGS
# ============================================================

def delete_mappings(source_message_id):

    conn = sqlite3.connect(DB_FILE, timeout=30)

    try:

        conn.execute("""
            DELETE FROM message_map

            WHERE source_chat_id = ?
              AND source_message_id = ?

        """, (
            SOURCE_CHAT_ID,
            source_message_id
        ))

        conn.commit()

    finally:
        conn.close()


# ============================================================
# TELETHON
# ============================================================

client = TelegramClient(
    "lex_publisher",
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

            logger.warning(
                "FloodWait: %s seconds",
                e.seconds
            )

            await asyncio.sleep(e.seconds + 1)

        except RPCError as e:

            logger.error(
                "Telegram RPC error: %s",
                e
            )

            if attempt == 2:
                return None

            await asyncio.sleep(2)

        except Exception as e:

            logger.exception(
                "Operation error: %s",
                e
            )

            if attempt == 2:
                return None

            await asyncio.sleep(2)

    return None


# ============================================================
# COPY MESSAGE TO ONE TARGET
# ============================================================

async def copy_to_target(message, target_chat_id):

    text = message.raw_text or ""

    if not text.strip():
        return None

    sent = await run_with_retry(
        client.send_message,
        target_chat_id,
        text,
        formatting_entities=message.entities
    )

    if sent is None:
        return None

    return sent.id


# ============================================================
# COPY TO ALL 5 GROUPS
# ============================================================

async def publish_message(message):

    logger.info(
        "PUBLISH SOURCE MESSAGE: %s",
        message.id
    )

    success = 0

    for target_chat_id in TARGET_CHAT_IDS:

        target_message_id = await copy_to_target(
            message,
            target_chat_id
        )

        if target_message_id is None:

            logger.error(
                "COPY FAILED | SOURCE=%s | TARGET=%s",
                message.id,
                target_chat_id
            )

            continue

        save_mapping(
            message.id,
            target_chat_id,
            target_message_id
        )

        success += 1

        logger.info(
            "COPIED | SOURCE=%s -> TARGET=%s:%s",
            message.id,
            target_chat_id,
            target_message_id
        )

        await asyncio.sleep(0.2)

    logger.info(
        "PUBLISH COMPLETE | SOURCE=%s | %s/%s",
        message.id,
        success,
        len(TARGET_CHAT_IDS)
    )


# ============================================================
# NEW MESSAGE
# ============================================================

@client.on(events.NewMessage(chats=SOURCE_CHAT_ID))
async def source_new_message(event):

    try:

        message = event.message

        # منع البوت من نسخ رسائله الخاصة
        if BOT_ID is not None:
            if event.sender_id == BOT_ID:
                return

        text = message.raw_text or ""

        # تجاهل الرسائل الفارغة
        if not text.strip():
            return

        # تجاهل الأوامر
        if text.startswith("/"):
            return

        logger.info(
            "NEW SOURCE MESSAGE | id=%s | sender=%s",
            message.id,
            event.sender_id
        )

        await publish_message(message)

    except Exception as e:

        logger.exception(
            "NEW MESSAGE HANDLER ERROR: %s",
            e
        )


# ============================================================
# EDIT MESSAGE
# ============================================================

@client.on(events.MessageEdited(chats=SOURCE_CHAT_ID))
async def source_edit_message(event):

    try:

        message = event.message

        text = message.raw_text or ""

        if not text.strip():
            return

        if text.startswith("/"):
            return

        mappings = get_mappings(message.id)

        if not mappings:

            logger.warning(
                "NO MAPPING FOR EDIT | SOURCE=%s",
                message.id
            )

            return

        logger.info(
            "EDIT SOURCE MESSAGE | id=%s | targets=%s",
            message.id,
            len(mappings)
        )

        for target_chat_id, target_message_id in mappings:

            result = await run_with_retry(
                client.edit_message,
                target_chat_id,
                target_message_id,
                text,
                formatting_entities=message.entities
            )

            if result is not None:

                logger.info(
                    "EDITED | SOURCE=%s -> TARGET=%s:%s",
                    message.id,
                    target_chat_id,
                    target_message_id
                )

            await asyncio.sleep(0.2)

    except Exception as e:

        logger.exception(
            "EDIT HANDLER ERROR: %s",
            e
        )


# ============================================================
# DELETE MESSAGE
# ============================================================

async def delete_source_message(source_message_id):

    mappings = get_mappings(
        source_message_id
    )

    if not mappings:

        logger.warning(
            "NO MAPPING FOR DELETE | SOURCE=%s",
            source_message_id
        )

        return

    logger.info(
        "DELETE SOURCE=%s | %s TARGETS",
        source_message_id,
        len(mappings)
    )

    for target_chat_id, target_message_id in mappings:

        result = await run_with_retry(
            client.delete_messages,
            target_chat_id,
            [target_message_id]
        )

        if result is not None:

            logger.info(
                "DELETED | SOURCE=%s -> TARGET=%s:%s",
                source_message_id,
                target_chat_id,
                target_message_id
            )

        await asyncio.sleep(0.2)

    delete_mappings(
        source_message_id
    )


# ============================================================
# TELETHON DELETE EVENT
# ============================================================

@client.on(events.MessageDeleted(chats=SOURCE_CHAT_ID))
async def source_deleted_message(event):

    try:

        logger.info(
            "DELETE EVENT | SOURCE=%s | IDS=%s",
            SOURCE_CHAT_ID,
            event.deleted_ids
        )

        for message_id in event.deleted_ids:

            await delete_source_message(
                message_id
            )

    except Exception as e:

        logger.exception(
            "DELETE HANDLER ERROR: %s",
            e
        )


# ============================================================
# /del - حذف يدوي (رد على الرسالة اللي تحب تحذفها)
# ============================================================

@client.on(
    events.NewMessage(
        chats=SOURCE_CHAT_ID,
        pattern=r"^/del$"
    )
)
async def del_handler(event):

    if not is_allowed(event.sender_id):
        return

    if not event.is_reply:

        await event.reply(
            "⚠️ خاصك ترد (Reply) على الرسالة اللي تحب تحذفها، وتكتب /del"
        )

        return

    replied = await event.get_reply_message()

    source_message_id = replied.id

    mappings = get_mappings(source_message_id)

    if not mappings:

        await event.reply(
            f"❌ ما لقيتش نسخ لهاذي الرسالة (id={source_message_id})."
        )

        return

    deleted_count = 0

    for target_chat_id, target_message_id in mappings:

        result = await run_with_retry(
            client.delete_messages,
            target_chat_id,
            [target_message_id]
        )

        if result is not None:
            deleted_count += 1

        await asyncio.sleep(0.2)

    delete_mappings(source_message_id)

    # نحذفو الرسالة الأصلية زادة من المجموعة المخصصة
    try:
        await client.delete_messages(SOURCE_CHAT_ID, [source_message_id])
    except Exception as e:
        logger.warning("ما قدرتش نحذف الرسالة الأصلية: %s", e)

    # نحذفو أمر /del نفسه
    try:
        await event.delete()
    except Exception:
        pass

    logger.info(
        "MANUAL DELETE | SOURCE=%s | %s/%s TARGETS",
        source_message_id,
        deleted_count,
        len(mappings)
    )


# ============================================================
# /status
# ============================================================

@client.on(
    events.NewMessage(
        chats=SOURCE_CHAT_ID,
        pattern=r"^/status$"
    )
)
async def status_handler(event):

    if not is_allowed(event.sender_id):
        return

    await event.reply(
        "🤖 LEX AUTO PUBLISHER PRO\n\n"
        "🟢 STATUS: ONLINE\n\n"
        f"👤 ALLOWED USERS:\n"
        + "\n".join(f"`{uid}`" for uid in ALLOWED_USER_IDS)
        + "\n\n"
        f"🏠 SOURCE:\n`{SOURCE_CHAT_ID}`\n\n"
        "📤 TARGETS:\n"
        + "\n".join(
            f"`{chat_id}`"
            for chat_id in TARGET_CHAT_IDS
        )
        + "\n\n"
        "📝 TEXT ONLY\n"
        "📤 AUTO PUBLISH: ON\n"
        "✏️ EDIT SYNC: ON\n"
        "🗑 DELETE SYNC: ON"
    )


# ============================================================
# /id
# ============================================================

@client.on(
    events.NewMessage(
        pattern=r"^/id$"
    )
)
async def id_handler(event):

    if not is_allowed(event.sender_id):
        return

    await event.reply(
        f"🆔 CHAT ID:\n`{event.chat_id}`"
    )


# ============================================================
# START
# ============================================================

async def main():

    global BOT_ID

    init_db()

    logger.info(
        "========================================"
    )

    logger.info(
        "LEX AUTO PUBLISHER PRO"
    )

    logger.info(
        "SOURCE: %s",
        SOURCE_CHAT_ID
    )

    logger.info(
        "TARGETS: %s",
        TARGET_CHAT_IDS
    )

    # تسجيل دخول البوت
    await client.start(
        bot_token=BOT_TOKEN
    )

    me = await client.get_me()

    BOT_ID = me.id

    logger.info(
        "BOT ID: %s",
        BOT_ID
    )

    logger.info(
        "USERNAME: @%s",
        getattr(me, "username", "")
    )

    logger.info(
        "STATUS: ONLINE"
    )

    logger.info(
        "========================================"
    )

    await client.run_until_disconnected()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        logger.info(
            "LEX STOPPED"
        )
 
