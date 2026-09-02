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

# IDs المسموح لهم باستعمال أوامر البوت
OWNER_ID = os.getenv("OWNER_ID", "")

OWNER_IDS = {
    int(x.strip())
    for x in OWNER_ID.split(",")
    if x.strip()
}

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
# DELETE COMMAND
# ============================================================

DELETE_COMMAND = "Merchantdz_bot"

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
            source_chat_id = ?
            AND source_message_id = ?
    """, (
        source_chat_id,
        source_message_id
    ))

    conn.commit()
    conn.close()


# ============================================================
# PERMISSION
# ============================================================

def is_allowed(user_id):

    return user_id in OWNER_IDS


# ============================================================
# TELEGRAM CLIENT
# ============================================================

client = TelegramClient(
    "lex_publisher_session_2",
    API_ID,
    API_HASH
)


# ============================================================
# RETRY SYSTEM
# ============================================================

async def run_with_retry(
    func,
    *args,
    **kwargs
):

    while True:

        try:

            return await func(
                *args,
                **kwargs
            )

        except FloodWaitError as e:

            logger.warning(
                "FLOOD WAIT: %s seconds",
                e.seconds
            )

            await asyncio.sleep(
                e.seconds + 2
            )

        except RPCError as e:

            logger.warning(
                "RPC ERROR: %s",
                e
            )

            await asyncio.sleep(2)

        except Exception as e:

            logger.exception(
                "RETRY ERROR: %s",
                e
            )

            await asyncio.sleep(2)


# ============================================================
# SOURCE -> TARGETS
# ============================================================

@client.on(
    events.NewMessage(
        chats=SOURCE_CHAT_ID
    )
)
async def source_new_message(event):

    try:

        text = event.raw_text or ""

        # تجاهل جميع أوامر البوت
        if text.startswith("/"):
            return

        source_message_id = event.id

        logger.info(
            "NEW SOURCE MESSAGE: %s",
            source_message_id
        )

        # ----------------------------------------------------
        # نشر في كل Target
        # ----------------------------------------------------

        for target_chat_id in TARGET_CHAT_IDS:

            reply_to = None

            # ------------------------------------------------
            # الحفاظ على Reply
            # ------------------------------------------------

            if event.is_reply:

                replied = await event.get_reply_message()

                if replied:

                    reply_to = get_parent_mapping(
                        SOURCE_CHAT_ID,
                        replied.id,
                        target_chat_id
                    )

            # ------------------------------------------------
            # إرسال
            # ------------------------------------------------

            sent = await run_with_retry(
                client.send_message,
                target_chat_id,
                event.message,
                reply_to=reply_to
            )

            if sent:

                save_mapping(
                    SOURCE_CHAT_ID,
                    source_message_id,
                    target_chat_id,
                    sent.id
                )

                logger.info(
                    "PUBLISHED | SOURCE=%s | TARGET=%s | MSG=%s",
                    source_message_id,
                    target_chat_id,
                    sent.id
                )

            await asyncio.sleep(0.3)

    except Exception as e:

        logger.exception(
            "SOURCE NEW ERROR: %s",
            e
        )


# ============================================================
# EDIT SOURCE
# ============================================================

@client.on(
    events.MessageEdited(
        chats=SOURCE_CHAT_ID
    )
)
async def source_message_edited(event):

    try:

        text = event.raw_text or ""

        if text.startswith("/"):
            return

        mappings = get_mappings(
            event.id,
            SOURCE_CHAT_ID
        )

        if not mappings:
            return

        for target_chat_id, target_message_id in mappings:

            try:

                await run_with_retry(
                    client.edit_message,
                    target_chat_id,
                    target_message_id,
                    event.message
                )

                logger.info(
                    "EDITED | SOURCE=%s | TARGET=%s",
                    event.id,
                    target_message_id
                )

            except Exception as e:

                logger.warning(
                    "EDIT FAILED: %s",
                    e
                )

            await asyncio.sleep(0.3)

    except Exception as e:

        logger.exception(
            "SOURCE EDIT ERROR: %s",
            e
        )


# ============================================================
# DELETE SOURCE AUTOMATICALLY
# ============================================================

@client.on(
    events.MessageDeleted(
        chats=SOURCE_CHAT_ID
    )
)
async def source_message_deleted(event):

    try:

        for source_message_id in event.deleted_ids:

            mappings = get_mappings(
                source_message_id,
                SOURCE_CHAT_ID
            )

            if not mappings:
                continue

            for target_chat_id, target_message_id in mappings:

                try:

                    await run_with_retry(
                        client.delete_messages,
                        target_chat_id,
                        [target_message_id]
                    )

                    logger.info(
                        "AUTO DELETE | TARGET=%s | MSG=%s",
                        target_chat_id,
                        target_message_id
                    )

                except Exception as e:

                    logger.warning(
                        "AUTO DELETE FAILED: %s",
                        e
                    )

                await asyncio.sleep(0.3)

            delete_mappings(
                source_message_id,
                SOURCE_CHAT_ID
            )

    except Exception as e:

        logger.exception(
            "SOURCE DELETE ERROR: %s",
            e
        )


# ============================================================
# MANUAL DELETE
#
# Reply على المنشور ثم:
#
# /del
#
# أو:
#
# /del@Merchantdz_bot
#
# ============================================================

@client.on(
    events.NewMessage(
        chats=SOURCE_CHAT_ID
    )
)
async def manual_delete_handler(event):

    try:

        text = (
            event.raw_text or ""
        ).strip()

        # ----------------------------------------------------
        # نقبل فقط:
        #
        # /del
        # /del@Merchantdz_bot
        # ----------------------------------------------------

        if not re.fullmatch(
            rf"/del(?:@{re.escape(DELETE_COMMAND)})?",
            text,
            re.IGNORECASE
        ):
            return

        logger.info(
            "DELETE COMMAND DETECTED: %s",
            text
        )

        # ----------------------------------------------------
        # Permission
        # ----------------------------------------------------

        if not is_allowed(
            event.sender_id
        ):

            logger.warning(
                "UNAUTHORIZED DELETE | USER=%s",
                event.sender_id
            )

            return

        # ----------------------------------------------------
        # يجب أن يكون Reply
        # ----------------------------------------------------

        if not event.is_reply:

            await event.reply(
                "⚠️ لازم تدير Reply على المنشور "
                "اللي حاب تحذفه.\n\n"
                "ثم اكتب:\n"
                "/del@Merchantdz_bot"
            )

            return

        # ----------------------------------------------------
        # الحصول على الرسالة الأصلية
        # ----------------------------------------------------

        replied = await event.get_reply_message()

        if replied is None:

            await event.reply(
                "❌ ما قدرتش نحدد الرسالة الأصلية."
            )

            return

        source_message_id = replied.id

        logger.info(
            "MANUAL DELETE REQUEST | SOURCE=%s | USER=%s",
            source_message_id,
            event.sender_id
        )

        # ----------------------------------------------------
        # البحث في lex_publisher_2.db
        # ----------------------------------------------------

        mappings = get_mappings(
            source_message_id,
            SOURCE_CHAT_ID
        )

        if not mappings:

            await event.reply(
                f"❌ ما لقيتش نسخ لهذه الرسالة.\n\n"
                f"Source ID: {source_message_id}\n"
                f"Database: {DB_FILE}"
            )

            return

        total = len(mappings)
        deleted = 0

        # ----------------------------------------------------
        # حذف النسخ من Targets
        # ----------------------------------------------------

        for target_chat_id, target_message_id in mappings:

            try:

                result = await run_with_retry(
                    client.delete_messages,
                    target_chat_id,
                    [target_message_id]
                )

                # إذا لم يرمي Telethon خطأ نعتبر العملية ناجحة
                if result is not None:
                    deleted += 1

                logger.info(
                    "MANUAL DELETE | TARGET=%s | MSG=%s",
                    target_chat_id,
                    target_message_id
                )

            except Exception as e:

                logger.warning(
                    "TARGET DELETE FAILED | "
                    "TARGET=%s | MSG=%s | ERROR=%s",
                    target_chat_id,
                    target_message_id,
                    e
                )

            await asyncio.sleep(0.3)

        # ----------------------------------------------------
        # حذف Database mappings
        # ----------------------------------------------------

        delete_mappings(
            source_message_id,
            SOURCE_CHAT_ID
        )

        # ----------------------------------------------------
        # حذف المنشور الأصلي
        # ----------------------------------------------------

        try:

            await run_with_retry(
                client.delete_messages,
                SOURCE_CHAT_ID,
                [source_message_id]
            )

            logger.info(
                "SOURCE MESSAGE DELETED: %s",
                source_message_id
            )

        except Exception as e:

            logger.warning(
                "SOURCE DELETE FAILED: %s",
                e
            )

        # ----------------------------------------------------
        # حذف رسالة الأمر نفسها
        # ----------------------------------------------------

        try:

            await event.delete()

        except Exception as e:

            logger.warning(
                "DELETE COMMAND MESSAGE FAILED: %s",
                e
            )

        logger.info(
            "MANUAL DELETE COMPLETE | "
            "SOURCE=%s | DELETED=%s/%s | DB=%s",
            source_message_id,
            deleted,
            total,
            DB_FILE
        )

    except Exception as e:

        logger.exception(
            "MANUAL DELETE ERROR: %s",
            e
        )


# ============================================================
# STATUS
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
        "🟢 LEX AUTO PUBLISHER PRO - BOT 2\n\n"
        f"Bot: {BOT_NAME}\n"
        f"Source: {SOURCE_CHAT_ID}\n"
        f"Targets: {len(TARGET_CHAT_IDS)}\n"
        f"Database: {DB_FILE}\n"
        f"Delete: /del@{DELETE_COMMAND}"
    )


# ============================================================
# ID
# ============================================================

@client.on(
    events.NewMessage(
        chats=SOURCE_CHAT_ID,
        pattern=r"^/id$"
    )
)
async def id_handler(event):

    if not is_allowed(
        event.sender_id
    ):
        return

    await event.reply(
        f"👤 Your ID:\n"
        f"`{event.sender_id}`\n\n"
        f"💬 Chat ID:\n"
        f"`{SOURCE_CHAT_ID}`"
    )


# ============================================================
# SPECIAL CHANNELS
# ============================================================

def register_special_channel(
    channel_id,
    owner_id
):

    # --------------------------------------------------------
    # SPECIAL NEW
    # --------------------------------------------------------

    @client.on(
        events.NewMessage(
            chats=channel_id
        )
    )
    async def special_new(event):

        try:

            text = event.raw_text or ""

            if text.startswith("/"):
                return

            source_message_id = event.id

            logger.info(
                "SPECIAL NEW | CHANNEL=%s | MSG=%s | OWNER=%s",
                channel_id,
                source_message_id,
                owner_id
            )

            for target_chat_id in TARGET_CHAT_IDS:

                reply_to = None

                if event.is_reply:

                    replied = await event.get_reply_message()

                    if replied:

                        reply_to = get_parent_mapping(
                            channel_id,
                            replied.id,
                            target_chat_id
                        )

                sent = await run_with_retry(
                    client.send_message,
                    target_chat_id,
                    event.message,
                    reply_to=reply_to
                )

                if sent:

                    save_mapping(
                        channel_id,
                        source_message_id,
                        target_chat_id,
                        sent.id
                    )

                await asyncio.sleep(0.3)

        except Exception as e:

            logger.exception(
                "SPECIAL NEW ERROR: %s",
                e
            )

    # --------------------------------------------------------
    # SPECIAL EDIT
    # --------------------------------------------------------

    @client.on(
        events.MessageEdited(
            chats=channel_id
        )
    )
    async def special_edit(event):

        try:

            mappings = get_mappings(
                event.id,
                channel_id
            )

            if not mappings:
                return

            for target_chat_id, target_message_id in mappings:

                try:

                    await run_with_retry(
                        client.edit_message,
                        target_chat_id,
                        target_message_id,
                        event.message
                    )

                except Exception as e:

                    logger.warning(
                        "SPECIAL EDIT FAILED: %s",
                        e
                    )

                await asyncio.sleep(0.3)

        except Exception as e:

            logger.exception(
                "SPECIAL EDIT ERROR: %s",
                e
            )

    # --------------------------------------------------------
    # SPECIAL DELETE
    # --------------------------------------------------------

    @client.on(
        events.MessageDeleted(
            chats=channel_id
        )
    )
    async def special_delete(event):

        try:

            for source_message_id in event.deleted_ids:

                mappings = get_mappings(
                    source_message_id,
                    channel_id
                )

                if not mappings:
                    continue

                for target_chat_id, target_message_id in mappings:

                    try:

                        await run_with_retry(
                            client.delete_messages,
                            target_chat_id,
                            [target_message_id]
                        )

                    except Exception as e:

                        logger.warning(
                            "SPECIAL DELETE FAILED: %s",
                            e
                        )

                    await asyncio.sleep(0.3)

                delete_mappings(
                    source_message_id,
                    channel_id
                )

        except Exception as e:

            logger.exception(
                "SPECIAL DELETE ERROR: %s",
                e
            )


# ============================================================
# REGISTER SPECIAL CHANNELS
# ============================================================

for channel_id, owner_id in SPECIAL_CHANNELS.items():

    register_special_channel(
        channel_id,
        owner_id
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    # إنشاء قاعدة البيانات
    init_db()

    logger.info(
        "=================================================="
    )

    logger.info(
        "LEX AUTO PUBLISHER PRO - BOT 2"
    )

    logger.info(
        "DATABASE: %s",
        DB_FILE
    )

    logger.info(
        "SOURCE: %s",
        SOURCE_CHAT_ID
    )

    logger.info(
        "TARGETS: %s",
        TARGET_CHAT_IDS
    )

    logger.info(
        "DELETE COMMAND: /del@%s",
        DELETE_COMMAND
    )

    logger.info(
        "=================================================="
    )

    # --------------------------------------------------------
    # تشغيل البوت
    # --------------------------------------------------------

    await client.start(
        bot_token=BOT_TOKEN
    )

    me = await client.get_me()

    logger.info(
        "BOT CONNECTED: @%s | ID=%s",
        me.username,
        me.id
    )

    logger.info(
        "BOT 2 ONLINE 🟢"
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
            "BOT 2 STOPPED"
        )
