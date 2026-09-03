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
# ALLOWED USERS (OWNERS - صلاحيات كاملة)
# ============================================================

ALLOWED_USER_IDS = [
    int(x.strip())
    for x in os.environ["OWNER_ID"].split(",")
    if x.strip()
]


def is_allowed(user_id):
    """Owner رئيسي - صلاحيات كاملة على SOURCE_CHAT_ID"""
    return user_id in ALLOWED_USER_IDS


# ============================================================
# SOURCE & TARGETS
# ============================================================

SOURCE_CHAT_ID = int(os.environ["SOURCE_CHAT_ID"])

# قناة إضافية: النشر منها مسموح فقط للقناة الخاصة بهذا الشخص
# (هاد الميكانيزم بقا خاص فقط بـ -1002239341307 / صاحبها 5578623360)
SPECIAL_CHANNELS = {
    -1002239341307: 5578623360,
}


def is_channel_owner(user_id, chat_id):
    """ واش هاد اليوزر هو صاحب القناة الخاصة (SPECIAL_CHANNELS) اللي الأمر توجه منها. صاحب القناة يقدر غير يدير /del و/status على الرسائل اللي جاية من قناته هو بالضبط. """
    return SPECIAL_CHANNELS.get(chat_id) == user_id


# ============================================================
# PERSONAL CHANNEL COPY
#
# صديقك كيكتب عادي فـ SOURCE_CHAT_ID بحال الجميع، والبوت كيبعث
# رسالتو للمجموعات (TARGET_CHAT_IDS) بحال العادة. زيادة على هادشي،
# كيبعث البوت copy زايدة ديال رسالتو هو بالضبط لقناته الخاصة.
#
# user_id -> channel_id (القناة ديالو هو غير)
# ============================================================

PERSONAL_CHANNELS = {
    1760181851: -1002895996910,
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
        conn.execute(""" CREATE TABLE IF NOT EXISTS message_map ( source_chat_id INTEGER NOT NULL, source_message_id INTEGER NOT NULL, target_chat_id INTEGER NOT NULL, target_message_id INTEGER NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY ( source_chat_id, source_message_id, target_chat_id ) ) """)
        conn.commit()
    finally:
        conn.close()


def save_mapping(source_message_id, target_chat_id, target_message_id, source_chat_id=SOURCE_CHAT_ID):
    conn = sqlite3.connect(DB_FILE, timeout=30)
    try:
        conn.execute(""" INSERT OR REPLACE INTO message_map ( source_chat_id, source_message_id, target_chat_id, target_message_id ) VALUES (?, ?, ?, ?) """, (
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
        return conn.execute(""" SELECT target_chat_id, target_message_id FROM message_map WHERE source_chat_id = ? AND source_message_id = ? ORDER BY target_chat_id """, (
            source_chat_id,
            source_message_id
        )).fetchall()
    finally:
        conn.close()


def delete_mappings(source_message_id, source_chat_id=SOURCE_CHAT_ID):
    conn = sqlite3.connect(DB_FILE, timeout=30)
    try:
        conn.execute(""" DELETE FROM message_map WHERE source_chat_id = ? AND source_message_id = ? """, (
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
    text = message.raw_text or ""

    if not text.strip():
        return None

    kwargs = {
        "formatting_entities": message.entities
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

async def publish_message(message, source_chat_id=SOURCE_CHAT_ID, extra_targets=None):
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

    # TARGETS العادية + أي targets زايدة (بحال القناة الخاصة الشخصية)
    all_targets = list(TARGET_CHAT_IDS) + list(extra_targets or [])

    success = 0

    for target_chat_id in all_targets:
        target_chat_id = int(target_chat_id)
        reply_to = parent_mappings.get(target_chat_id)

        if parent_source_id is not None and reply_to is None:
            logger.warning("NO PARENT MAPPING | SOURCE_PARENT=%s | TARGET=%s", parent_source_id, target_chat_id)

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
# SHARED DELETE HELPER (يستخدمها /del فـ SOURCE وفـ SPECIAL CHANNELS)
# ============================================================

async def delete_source_message(source_message_id, source_chat_id=SOURCE_CHAT_ID):
    mappings = get_mappings(source_message_id, source_chat_id)
    if not mappings:
        logger.warning("NO MAPPING FOR DELETE | SOURCE=%s", source_message_id)
        return 0

    logger.info("DELETE SOURCE=%s | %s TARGETS", source_message_id, len(mappings))

    deleted_count = 0

    for target_chat_id, target_message_id in mappings:
        result = await run_with_retry(
            client.delete_messages,
            target_chat_id,
            [target_message_id]
        )
        if result is not None:
            deleted_count += 1
            logger.info("DELETED | SOURCE=%s -> TARGET=%s:%s", source_message_id, target_chat_id, target_message_id)
        await asyncio.sleep(0.3)

    delete_mappings(source_message_id, source_chat_id)

    return deleted_count


async def handle_manual_delete(event, source_chat_id):
    """ منطق /del المشترك: كيتأكد Reply، كيجيب mappings من نفس source_chat_id (يعني كل قناة كتقدر تمسح غير الرسائل اللي خرجت منها هي)، كيمسح النسخ فـ targets، الرسالة الأصلية، وأمر /del نفسو. """
    if not event.is_reply:
        await event.reply("⚠️ خاصك تدير Reply على الرسالة اللي تحب تحذفها وتكتب /del")
        return

    replied = await event.get_reply_message()
    if replied is None:
        return

    source_message_id = replied.id
    mappings = get_mappings(source_message_id, source_chat_id)
    if not mappings:
        await event.reply(f"❌ ما لقيتش نسخة للرسالة (id={source_message_id})")
        return

    deleted_count = await delete_source_message(source_message_id, source_chat_id)

    try:
        await client.delete_messages(source_chat_id, [source_message_id])
    except Exception as e:
        logger.warning("SOURCE DELETE FAILED: %s", e)

    try:
        await event.delete()
    except Exception:
        pass

    logger.info(
        "MANUAL DELETE | CHAT=%s | SOURCE=%s | %s/%s",
        source_chat_id,
        source_message_id,
        deleted_count,
        len(mappings)
    )


# ============================================================
# EVENT HANDLERS - SOURCE_CHAT_ID
# ============================================================

@client.on(events.NewMessage(chats=SOURCE_CHAT_ID))
async def source_new_message(event):
    try:
        message = event.message
        if BOT_ID is not None and event.sender_id == BOT_ID:
            return

        text = message.raw_text or ""
        if not text.strip() or text.startswith("/"):
            return

        logger.info("NEW SOURCE MESSAGE | ID=%s | SENDER=%s | REPLY_TO=%s", message.id, event.sender_id, message.reply_to_msg_id)

        personal_channel = PERSONAL_CHANNELS.get(event.sender_id)
        extra_targets = [personal_channel] if personal_channel else None

        if personal_channel:
            logger.info(
                "PERSONAL COPY ENABLED | SENDER=%s | CHANNEL=%s",
                event.sender_id,
                personal_channel
            )

        await publish_message(message, extra_targets=extra_targets)
    except Exception as e:
        logger.exception("NEW MESSAGE ERROR: %s", e)


@client.on(events.MessageEdited(chats=SOURCE_CHAT_ID))
async def source_edit_message(event):
    try:
        message = event.message
        text = message.raw_text or ""
        if not text.strip() or text.startswith("/"):
            return

        mappings = get_mappings(message.id)
        if not mappings:
            logger.warning("NO MAPPING FOR EDIT | SOURCE=%s", message.id)
            return

        logger.info("EDIT SOURCE MESSAGE | SOURCE=%s | TARGETS=%s", message.id, len(mappings))

        for target_chat_id, target_message_id in mappings:
            result = await run_with_retry(
                client.edit_message,
                target_chat_id,
                target_message_id,
                text,
                formatting_entities=message.entities
            )
            if result is not None:
                logger.info("EDITED | SOURCE=%s -> TARGET=%s:%s", message.id, target_chat_id, target_message_id)
            await asyncio.sleep(0.3)
    except Exception as e:
        logger.exception("EDIT HANDLER ERROR: %s", e)


@client.on(events.MessageDeleted(chats=SOURCE_CHAT_ID))
async def source_deleted_message(event):
    try:
        logger.info("DELETE EVENT | SOURCE=%s | IDS=%s", SOURCE_CHAT_ID, event.deleted_ids)
        for message_id in event.deleted_ids:
            await delete_source_message(message_id)
    except Exception as e:
        logger.exception("DELETE HANDLER ERROR: %s", e)


@client.on(events.NewMessage(chats=SOURCE_CHAT_ID, pattern=r"^/del$"))
async def del_handler(event):
    # /del فـ SOURCE_CHAT_ID مخصص فقط لـ owner الرئيسي
    if not is_allowed(event.sender_id):
        return
    await handle_manual_delete(event, SOURCE_CHAT_ID)


@client.on(events.NewMessage(chats=SOURCE_CHAT_ID, pattern=r"^/status$"))
async def status_handler(event):
    if not is_allowed(event.sender_id):
        return

    await event.reply(
        f"🤖 LEX AUTO PUBLISHER PRO ({BOT_NAME})\n\n"
        "🟢 STATUS: ONLINE\n\n"
        "🏠 SOURCE:\n"
        f"`{SOURCE_CHAT_ID}`\n\n"
        "📤 TARGETS:\n"
        + "\n".join(f"`{chat_id}`" for chat_id in TARGET_CHAT_IDS)
        + "\n\n"
        f"🗄 DB: `{DB_FILE}`"
    )


@client.on(events.NewMessage(pattern=r"^/id$"))
async def id_handler(event):
    if not (is_allowed(event.sender_id) or is_channel_owner(event.sender_id, event.chat_id)):
        return
    await event.reply(f"🆔 CHAT ID:\n`{event.chat_id}`")


# ============================================================
# SPECIAL ADDITIONAL CHANNELS
# ============================================================

for _special_chat_id, _special_owner_id in SPECIAL_CHANNELS.items():

@client.on(events.NewMessage(chats=_special_chat_id))
    async def special_channel_new_message(event, special_chat_id=_special_chat_id, special_owner_id=_special_owner_id):
        try:
            message = event.message
            text = message.raw_text or ""
            if not text.strip() or text.startswith("/"):
                return

            logger.info(
                "NEW SPECIAL CHANNEL MESSAGE | CHANNEL=%s | ID=%s | SENDER=%s | OWNER=%s | REPLY_TO=%s",
                special_chat_id,
                message.id,
                event.sender_id,
                special_owner_id,
                message.reply_to_msg_id
            )

            await publish_message(message, special_chat_id)
        except Exception as e:
            logger.exception("SPECIAL CHANNEL NEW MESSAGE ERROR: %s", e)

@client.on(events.MessageEdited(chats=_special_chat_id))
    async def special_channel_edit_message(event, special_chat_id=_special_chat_id):
        try:
            message = event.message
            text = message.raw_text or ""
            if not text.strip() or text.startswith("/"):
                return

            mappings = get_mappings(message.id, special_chat_id)
            if not mappings:
                logger.warning("NO SPECIAL MAPPING FOR EDIT | SOURCE=%s", message.id)
                return

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
                        "SPECIAL EDITED | SOURCE=%s -> TARGET=%s:%s",
                        message.id, target_chat_id, target_message_id
                    )
                await asyncio.sleep(0.3)
        except Exception as e:
            logger.exception("SPECIAL EDIT HANDLER ERROR: %s", e)

@client.on(events.MessageDeleted(chats=_special_chat_id))
    async def special_channel_deleted_message(event, special_chat_id=_special_chat_id):
        try:
            logger.info(
                "SPECIAL DELETE EVENT | SOURCE=%s | IDS=%s",
                special_chat_id,
                event.deleted_ids
            )
            for message_id in event.deleted_ids:
                await delete_source_message(message_id, special_chat_id)
        except Exception as e:
            logger.exception("SPECIAL DELETE HANDLER ERROR: %s", e)

    # --------------------------------------------------------
    # /del خاص بصاحب هاد القناة: يقدر يمسح غير الرسائل اللي
    # خرجت من قناته هو (special_chat_id) - ماعندوش حتى صلاحية
    # على SOURCE_CHAT_ID ولا على قنوات special أخرى.
    # الـ owner الرئيسي يقدر يستخدمها هنا زعما.
    # --------------------------------------------------------

@client.on(events.NewMessage(chats=_special_chat_id, pattern=r"^/del$"))
    async def special_del_handler(event, special_chat_id=_special_chat_id, special_owner_id=_special_owner_id):
        if not (event.sender_id == special_owner_id or is_allowed(event.sender_id)):
            return
        await handle_manual_delete(event, special_chat_id)

@client.on(events.NewMessage(chats=_special_chat_id, pattern=r"^/status$"))
    async def special_status_handler(event, special_chat_id=_special_chat_id, special_owner_id=_special_owner_id):
        if not (event.sender_id == special_owner_id or is_allowed(event.sender_id)):
            return

        await event.reply(
            f"🤖 LEX AUTO PUBLISHER PRO ({BOT_NAME})\n\n"
            "🟢 STATUS: ONLINE\n\n"
            "🏠 القناة ديالك:\n"
            f"`{special_chat_id}`\n\n"
            "📤 TARGETS:\n"
            + "\n".join(f"`{chat_id}`" for chat_id in TARGET_CHAT_IDS)
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
    logger.info("SPECIAL CHANNELS: %s", SPECIAL_CHANNELS)
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
