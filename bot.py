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
# SOURCE -> MULTIPLE TARGET GROUPS
# REPLY + EDIT + DELETE SYNC
# ============================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]

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
# SOURCE
# ============================================================

SOURCE_CHAT_ID = int(
    os.environ["SOURCE_CHAT_ID"]
)


# ============================================================
# TARGETS
# ============================================================

TARGET_CHAT_IDS = [
    int(x.strip())
    for x in os.environ["TARGET_CHAT_IDS"].split(",")
    if x.strip()
]


# ============================================================
# DATABASE
# ============================================================

DB_FILE = os.getenv(
    "DB_FILE",
    "lex_publisher.db"
)


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

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

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

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

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
# GET MAPPINGS
# ============================================================

def get_mappings(source_message_id):

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    try:

        return conn.execute("""
            SELECT
                target_chat_id,
                target_message_id

            FROM message_map

            WHERE source_chat_id = ?
              AND source_message_id = ?

            ORDER BY target_chat_id
        """, (
            SOURCE_CHAT_ID,
            source_message_id
        )).fetchall()

    finally:

        conn.close()


# ============================================================
# DELETE MAPPINGS
# ============================================================

def delete_mappings(source_message_id):

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

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
# RETRY
# ============================================================

async def run_with_retry(
    func,
    *args,
    **kwargs
):

    for attempt in range(3):

        try:

            return await func(
                *args,
                **kwargs
            )

        except FloodWaitError as e:

            logger.warning(
                "FloodWait: %s seconds",
                e.seconds
            )

            await asyncio.sleep(
                e.seconds + 1
            )

        except RPCError as e:

            logger.error(
                "Telegram RPC error: %s",
                e
            )

            if attempt >= 2:
                return None

            await asyncio.sleep(2)

        except Exception as e:

            logger.exception(
                "Operation error: %s",
                e
            )

            if attempt >= 2:
                return None

            await asyncio.sleep(2)

    return None


# ============================================================
# SEND MESSAGE
# ============================================================

async def send_to_target(
    message,
    target_chat_id,
    reply_to=None
):

    text = message.raw_text or ""

    if not text.strip():
        return None

    kwargs = {
        "formatting_entities": message.entities
    }

    # ========================================================
    # IMPORTANT:
    # ONLY SET reply_to WHEN WE ACTUALLY HAVE
    # THE TARGET MESSAGE ID
    # ========================================================

    if reply_to is not None:

        kwargs["reply_to"] = reply_to

        logger.info(
            "REPLY SEND | TARGET=%s | REPLY_TO=%s",
            target_chat_id,
            reply_to
        )

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

async def publish_message(message):

    logger.info(
        "PUBLISH SOURCE MESSAGE: %s",
        message.id
    )

    # ========================================================
    # FIND PARENT MESSAGE
    # ========================================================

    parent_source_id = None

    if message.reply_to_msg_id:

        parent_source_id = (
            message.reply_to_msg_id
        )

        logger.info(
            "SOURCE REPLY DETECTED | "
            "MESSAGE=%s | PARENT=%s",
            message.id,
            parent_source_id
        )

    # ========================================================
    # GET TARGET MAPPINGS OF PARENT
    # ========================================================

    parent_mappings = {}

    if parent_source_id is not None:

        rows = get_mappings(
            parent_source_id
        )

        parent_mappings = {
            int(target_chat_id):
                int(target_message_id)

            for target_chat_id, target_message_id
            in rows
        }

        logger.info(
            "PARENT MAPPINGS FOUND: %s",
            parent_mappings
        )

    success = 0

    # ========================================================
    # SEND TO EVERY TARGET
    # ========================================================

    for target_chat_id in TARGET_CHAT_IDS:

        target_chat_id = int(
            target_chat_id
        )

        # ----------------------------------------------------
        # Find corresponding parent in THIS target
        # ----------------------------------------------------

        reply_to = parent_mappings.get(
            target_chat_id
        )

        if parent_source_id is not None:

            if reply_to is None:

                logger.warning(
                    "NO PARENT MAPPING | "
                    "SOURCE_PARENT=%s | TARGET=%s",
                    parent_source_id,
                    target_chat_id
                )

        # ----------------------------------------------------
        # Send
        # ----------------------------------------------------

        target_message_id = await send_to_target(
            message,
            target_chat_id,
            reply_to=reply_to
        )

        if target_message_id is None:

            logger.error(
                "COPY FAILED | SOURCE=%s | TARGET=%s",
                message.id,
                target_chat_id
            )

            continue

        # ----------------------------------------------------
        # SAVE MAPPING
        # ----------------------------------------------------

        save_mapping(
            message.id,
            target_chat_id,
            target_message_id
        )

        success += 1

        logger.info(
            "COPIED | SOURCE=%s -> "
            "TARGET=%s:%s",
            message.id,
            target_chat_id,
            target_message_id
        )

        await asyncio.sleep(0.3)

    logger.info(
        "PUBLISH COMPLETE | "
        "SOURCE=%s | %s/%s",
        message.id,
        success,
        len(TARGET_CHAT_IDS)
    )


# ============================================================
# NEW MESSAGE
# ============================================================

@client.on(
    events.NewMessage(
        chats=SOURCE_CHAT_ID
    )
)
async def source_new_message(event):

    try:

        message = event.message

        # ----------------------------------------------------
        # Ignore bot messages
        # ----------------------------------------------------

        if BOT_ID is not None:

            if event.sender_id == BOT_ID:
                return

        text = message.raw_text or ""

        # ----------------------------------------------------
        # Ignore empty messages
        # ----------------------------------------------------

        if not text.strip():
            return

        # ----------------------------------------------------
        # Ignore commands
        # ----------------------------------------------------

        if text.startswith("/"):
            return

        logger.info(
            "NEW SOURCE MESSAGE | "
            "ID=%s | SENDER=%s | REPLY_TO=%s",
            message.id,
            event.sender_id,
            message.reply_to_msg_id
        )

        await publish_message(
            message
        )

    except Exception as e:

        logger.exception(
            "NEW MESSAGE ERROR: %s",
            e
        )


# ============================================================
# EDIT MESSAGE
# ============================================================

@client.on(
    events.MessageEdited(
        chats=SOURCE_CHAT_ID
    )
)
async def source_edit_message(event):

    try:

        message = event.message

        text = message.raw_text or ""

        if not text.strip():
            return

        if text.startswith("/"):
            return

        mappings = get_mappings(
            message.id
        )

        if not mappings:

            logger.warning(
                "NO MAPPING FOR EDIT | "
                "SOURCE=%s",
                message.id
            )

            return

        logger.info(
            "EDIT SOURCE MESSAGE | "
            "SOURCE=%s | TARGETS=%s",
            message.id,
            len(mappings)
        )

        for (
            target_chat_id,
            target_message_id
        ) in mappings:

            result = await run_with_retry(
                client.edit_message,
                target_chat_id,
                target_message_id,
                text,
                formatting_entities=message.entities
            )

            if result is not None:

                logger.info(
                    "EDITED | "
                    "SOURCE=%s -> "
                    "TARGET=%s:%s",
                    message.id,
                    target_chat_id,
                    target_message_id
                )

            await asyncio.sleep(0.3)

    except Exception as e:

        logger.exception(
            "EDIT HANDLER ERROR: %s",
            e
        )


# ============================================================
# DELETE SOURCE MESSAGE
# ============================================================

async def delete_source_message(
    source_message_id
):

    mappings = get_mappings(
        source_message_id
    )

    if not mappings:

        logger.warning(
            "NO MAPPING FOR DELETE | "
            "SOURCE=%s",
            source_message_id
        )

        return

    logger.info(
        "DELETE SOURCE=%s | "
        "%s TARGETS",
        source_message_id,
        len(mappings)
    )

    for (
        target_chat_id,
        target_message_id
    ) in mappings:

        result = await run_with_retry(
            client.delete_messages,
            target_chat_id,
            [target_message_id]
        )

        if result is not None:

            logger.info(
                "DELETED | "
                "SOURCE=%s -> "
                "TARGET=%s:%s",
                source_message_id,
                target_chat_id,
                target_message_id
            )

        await asyncio.sleep(0.3)

    delete_mappings(
        source_message_id
    )


# ============================================================
# DELETE EVENT
# ============================================================

@client.on(
    events.MessageDeleted(
        chats=SOURCE_CHAT_ID
    )
)
async def source_deleted_message(event):

    try:

        logger.info(
            "DELETE EVENT | "
            "SOURCE=%s | IDS=%s",
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
# /del
# ============================================================

@client.on(
    events.NewMessage(
        chats=SOURCE_CHAT_ID,
        pattern=r"^/del$"
    )
)
async def del_handler(event):

    if not is_allowed(
        event.sender_id
    ):
        return

    if not event.is_reply:

        await event.reply(
            "⚠️ خاصك تدير Reply على الرسالة "
            "اللي تحب تحذفها وتكتب /del"
        )

        return

    replied = await event.get_reply_message()

    if replied is None:
        return

    source_message_id = replied.id

    mappings = get_mappings(
        source_message_id
    )

    if not mappings:

        await event.reply(
            f"❌ ما لقيتش نسخة للرسالة "
            f"(id={source_message_id})"
        )

        return

    deleted_count = 0

    for (
        target_chat_id,
        target_message_id
    ) in mappings:

        result = await run_with_retry(
            client.delete_messages,
            target_chat_id,
            [target_message_id]
        )

        if result is not None:
            deleted_count += 1

        await asyncio.sleep(0.3)

    delete_mappings(
        source_message_id
    )

    # حذف الأصل
    try:

        await client.delete_messages(
            SOURCE_CHAT_ID,
            [source_message_id]
        )

    except Exception as e:

        logger.warning(
            "SOURCE DELETE FAILED: %s",
            e
        )

    # حذف /del
    try:

        await event.delete()

    except Exception:
        pass

    logger.info(
        "MANUAL DELETE | "
        "SOURCE=%s | %s/%s",
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

    if not is_allowed(
        event.sender_id
    ):
        return

    await event.reply(
        "🤖 LEX AUTO PUBLISHER PRO\n\n"
        "🟢 STATUS: ONLINE\n\n"
        "👤 ALLOWED USERS:\n"
        +
        "\n".join(
            f"`{uid}`"
            for uid in ALLOWED_USER_IDS
        )
        +
        "\n\n"
        f"🏠 SOURCE:\n`{SOURCE_CHAT_ID}`\n\n"
        "📤 TARGETS:\n"
        +
        "\n".join(
            f"`{chat_id}`"
            for chat_id in TARGET_CHAT_IDS
        )
        +
        "\n\n"
        "📝 TEXT ONLY\n"
        "📤 AUTO PUBLISH: ON\n"
        "↩️ REPLY SYNC: ON\n"
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

    if not is_allowed(
        event.sender_id
    ):
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

    # --------------------------------------------------------
    # LOGIN BOT
    # --------------------------------------------------------

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
        getattr(
            me,
            "username",
            ""
        )
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

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logger.info(
            "LEX STOPPED"
        ) 
