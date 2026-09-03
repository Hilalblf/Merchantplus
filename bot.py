import os
import re
import sqlite3
import asyncio
import logging

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
# VARIABLES
# ============================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]

DB_FILE = os.getenv("DB_FILE", "lex_publisher_2.db")

OWNER_ID = int(os.getenv("OWNER_ID", "0"))

SOURCE_CHAT_ID = int(os.environ["SOURCE_CHAT_ID"])

# مثال:
# -1004333211848,-1004407774851,-1002470205630
TARGET_CHAT_IDS = [
    int(x.strip())
    for x in os.environ["TARGET_CHAT_IDS"].split(",")
    if x.strip()
]

# ============================================================
# USER BLOCKED TARGETS
#
# الصيغة:
#
# 5439488662:-1003376621047;
# 123456789:-1005555555555;
# 987654321:-1007777777777
#
# نفس الشخص يمكن منعه من عدة قنوات:
#
# 5439488662:-1003376621047,-1005555555555
# ============================================================

def parse_user_blocked_targets(value):
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
        except ValueError:
            logging.warning(
                f"Invalid user ID in USER_BLOCKED_TARGETS: {user_part}"
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
                    f"Invalid target ID in USER_BLOCKED_TARGETS: {target}"
                )

        if blocked_targets:
            result[user_id] = blocked_targets

    return result


USER_BLOCKED_TARGETS = parse_user_blocked_targets(
    os.getenv("USER_BLOCKED_TARGETS", "")
)

# ============================================================
# PERSONAL EXTRA CHANNEL
#
# شخص معين يمكنه النشر في قناة إضافية
# ============================================================

PERSONAL_USER_ID = int(
    os.getenv("PERSONAL_USER_ID", "0")
)

PERSONAL_CHANNEL_ID = int(
    os.getenv("PERSONAL_CHANNEL_ID", "0")
)

# ============================================================
# ADMIN IDS
#
# يمكن إضافة أكثر من Admin:
#
# ADMIN_IDS=123456789,987654321
# ============================================================

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip()
}

if OWNER_ID:
    ADMIN_IDS.add(OWNER_ID)

# ============================================================
# TELEGRAM CLIENT
# ============================================================

client = TelegramClient(
    "lex_publisher_2_session",
    API_ID,
    API_HASH
)

# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(
    DB_FILE,
    check_same_thread=False
)

db.execute("""
CREATE TABLE IF NOT EXISTS message_map (
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
    source_msg_id,
    target_chat_id,
    target_msg_id
):
    async with db_lock:
        db.execute(
            """
            INSERT OR REPLACE INTO message_map
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


async def get_mapping(
    source_msg_id,
    target_chat_id
):
    async with db_lock:
        cursor = db.execute(
            """
            SELECT target_msg_id
            FROM message_map
            WHERE source_msg_id = ?
            AND target_chat_id = ?
            """,
            (
                source_msg_id,
                target_chat_id
            )
        )

        row = cursor.fetchone()

    return row[0] if row else None


async def delete_mapping(
    source_msg_id,
    target_chat_id
):
    async with db_lock:
        db.execute(
            """
            DELETE FROM message_map
            WHERE source_msg_id = ?
            AND target_chat_id = ?
            """,
            (
                source_msg_id,
                target_chat_id
            )
        )

        db.commit()


# ============================================================
# TARGETS FOR USER
# ============================================================

def get_targets_for_user(
    sender_id,
    extra_targets=None
):
    targets = list(TARGET_CHAT_IDS)

    # قناة إضافية للشخص المحدد
    if (
        PERSONAL_USER_ID
        and PERSONAL_CHANNEL_ID
        and sender_id == PERSONAL_USER_ID
    ):
        targets.append(PERSONAL_CHANNEL_ID)

    # إزالة التكرار
    targets = list(dict.fromkeys(targets))

    # القنوات الممنوع منها هذا الشخص
    blocked = USER_BLOCKED_TARGETS.get(
        sender_id,
        set()
    )

    # حذف القنوات الممنوعة فقط لهذا الشخص
    targets = [
        target
        for target in targets
        if target not in blocked
    ]

    return targets


# ============================================================
# AUTHORIZATION
# ============================================================

def is_admin(user_id):
    if not user_id:
        return False

    return int(user_id) in ADMIN_IDS


# ============================================================
# SEND MESSAGE
# ============================================================

async def send_to_target(
    message,
    target_chat_id,
    reply_to=None
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
            f"FloodWait {e.seconds}s for {target_chat_id}"
        )

        await asyncio.sleep(e.seconds)

        sent = await client.send_message(
            entity=target_chat_id,
            message=message,
            reply_to=reply_to
        )

        return sent

    except RPCError as e:

        logging.error(
            f"Telegram error {target_chat_id}: {e}"
        )

        return None

    except Exception as e:

        logging.exception(
            f"Send error {target_chat_id}: {e}"
        )

        return None


# ============================================================
# PUBLISH MESSAGE
# ============================================================

async def publish_message(
    message,
    sender_id=None
):

    targets = get_targets_for_user(
        sender_id
    )

    if not targets:
        logging.info(
            f"No allowed targets for user {sender_id}"
        )
        return

    # --------------------------------------------------------
    # Find source reply parent
    # --------------------------------------------------------

    source_reply_id = None

    if message.is_reply:

        try:

            replied = await message.get_reply_message()

            if replied:
                source_reply_id = replied.id

        except Exception:
            source_reply_id = None

    # --------------------------------------------------------
    # Publish
    # --------------------------------------------------------

    for target_chat_id in targets:

        try:

            reply_to = None

            # إذا كان المنشور Reply
            # نحاول Reply على النسخة المقابلة في الهدف

            if source_reply_id:

                reply_to = await get_mapping(
                    source_reply_id,
                    target_chat_id
                )

            sent = await send_to_target(
                message,
                target_chat_id,
                reply_to=reply_to
            )

            if sent:

                await save_mapping(
                    message.id,
                    target_chat_id,
                    sent.id
                )

                logging.info(
                    f"Published "
                    f"{message.id} -> "
                    f"{target_chat_id} "
                    f"as {sent.id}"
                )

        except Exception as e:

            logging.exception(
                f"Publish failed "
                f"target={target_chat_id}: {e}"
            )


# ============================================================
# SOURCE NEW MESSAGE
# ============================================================

@client.on(events.NewMessage())
async def new_message_handler(event):

    try:

        chat_id = event.chat_id

        # فقط المصدر
        if chat_id != SOURCE_CHAT_ID:
            return

        message = event.message

        sender_id = event.sender_id

        logging.info(
            f"New source message "
            f"id={message.id} "
            f"sender={sender_id}"
        )

        await publish_message(
            message,
            sender_id=sender_id
        )

    except Exception as e:

        logging.exception(
            f"New message handler error: {e}"
        )


# ============================================================
# EDIT MESSAGE
# ============================================================

@client.on(events.MessageEdited())
async def edited_message_handler(event):

    try:

        if event.chat_id != SOURCE_CHAT_ID:
            return

        message = event.message

        logging.info(
            f"Edited source message {message.id}"
        )

        for target_chat_id in TARGET_CHAT_IDS:

            target_msg_id = await get_mapping(
                message.id,
                target_chat_id
            )

            if not target_msg_id:
                continue

            try:

                await client.edit_message(
                    entity=target_chat_id,
                    message=target_msg_id,
                    text=message.raw_text or ""
                )

            except RPCError as e:

                logging.error(
                    f"Edit failed "
                    f"{target_chat_id}: {e}"
                )

    except Exception as e:

        logging.exception(
            f"Edit handler error: {e}"
        )


# ============================================================
# DELETE SOURCE MESSAGE
# ============================================================

@client.on(events.MessageDeleted())
async def deleted_message_handler(event):

    try:

        if event.chat_id != SOURCE_CHAT_ID:
            return

        for source_msg_id in event.deleted_ids:

            logging.info(
                f"Deleted source message "
                f"{source_msg_id}"
            )

            # نحاول حذف النسخ من كل القنوات
            all_targets = set(TARGET_CHAT_IDS)

            if PERSONAL_CHANNEL_ID:
                all_targets.add(PERSONAL_CHANNEL_ID)

            for target_chat_id in all_targets:

                target_msg_id = await get_mapping(
                    source_msg_id,
                    target_chat_id
                )

                if not target_msg_id:
                    continue

                try:

                    await client.delete_messages(
                        target_chat_id,
                        [target_msg_id]
                    )

                    await delete_mapping(
                        source_msg_id,
                        target_chat_id
                    )

                    logging.info(
                        f"Deleted target "
                        f"{target_chat_id}:"
                        f"{target_msg_id}"
                    )

                except RPCError as e:

                    logging.error(
                        f"Delete failed "
                        f"{target_chat_id}: {e}"
                    )

    except Exception as e:

        logging.exception(
            f"Delete handler error: {e}"
        )


# ============================================================
# /ID
# ============================================================

@client.on(events.NewMessage(pattern=r"^/id$"))
async def id_handler(event):

    await event.reply(
        f"Chat ID: `{event.chat_id}`\n"
        f"User ID: `{event.sender_id}`"
    )


# ============================================================
# /STATUS
# ============================================================

@client.on(events.NewMessage(pattern=r"^/status$"))
async def status_handler(event):

    if not is_admin(event.sender_id):
        return

    blocked_count = sum(
        len(v)
        for v in USER_BLOCKED_TARGETS.values()
    )

    text = (
        "🟢 **LEX AUTO PUBLISHER PRO - BOT 2**\n\n"
        f"Source: `{SOURCE_CHAT_ID}`\n"
        f"Targets: `{len(TARGET_CHAT_IDS)}`\n"
        f"Blocked rules: `{blocked_count}`\n"
        f"DB: `{DB_FILE}`"
    )

    await event.reply(text)


# ============================================================
# /DEL
#
# /del
# /del@Merchantdz_bot
# /del 12345
# /del@Merchantdz_bot 12345
#
# إذا أرسلت /del كرد على منشور:
# يحذف المنشور المقابل في كل القنوات
# ============================================================

@client.on(
    events.NewMessage(
        pattern=r"^/del(?:@[\w_]+)?(?:\s+(\d+))?$"
    )
)
async def del_handler(event):

    if not is_admin(event.sender_id):
        return

    message_id = None

    # --------------------------------------------------------
    # إذا كتب /del 12345
    # --------------------------------------------------------

    match = event.pattern_match

    if match:
        value = match.group(1)

        if value:
            try:
                message_id = int(value)
            except ValueError:
                pass

    # --------------------------------------------------------
    # إذا كان /del كرد على رسالة
    # --------------------------------------------------------

    if message_id is None:

        if event.is_reply:

            try:

                replied = await event.get_reply_message()

                if replied:
                    message_id = replied.id

            except Exception:
                pass

    # --------------------------------------------------------
    # لا يوجد ID
    # --------------------------------------------------------

    if message_id is None:

        await event.reply(
            "❌ استعمل:\n"
            "`/del`\n"
            "أو\n"
            "`/del 12345`\n"
            "أو رد على المنشور بـ `/del`"
        )

        return

    # --------------------------------------------------------
    # Delete mapped messages
    # --------------------------------------------------------

    deleted = 0

    all_targets = set(TARGET_CHAT_IDS)

    if PERSONAL_CHANNEL_ID:
        all_targets.add(PERSONAL_CHANNEL_ID)

    for target_chat_id in all_targets:

        target_msg_id = await get_mapping(
            message_id,
            target_chat_id
        )

        if not target_msg_id:
            continue

        try:

            await client.delete_messages(
                target_chat_id,
                [target_msg_id]
            )

            await delete_mapping(
                message_id,
                target_chat_id
            )

            deleted += 1

        except Exception as e:

            logging.error(
                f"/del error "
                f"{target_chat_id}: {e}"
            )

    await event.reply(
        f"🗑️ تم حذف `{deleted}` نسخة."
    )


# ============================================================
# STARTUP
# ============================================================

async def main():

    logging.info("=" * 60)
    logging.info("LEX AUTO PUBLISHER PRO - BOT 2")
    logging.info("=" * 60)

    logging.info(
        f"Source: {SOURCE_CHAT_ID}"
    )

    logging.info(
        f"Targets: {TARGET_CHAT_IDS}"
    )

    logging.info(
        f"Blocked rules: {USER_BLOCKED_TARGETS}"
    )

    logging.info(
        f"Personal user: {PERSONAL_USER_ID}"
    )

    logging.info(
        f"Personal channel: {PERSONAL_CHANNEL_ID}"
    )

    await client.start(
        bot_token=BOT_TOKEN
    )

    me = await client.get_me()

    logging.info(
        f"Bot started: @{me.username}"
    )

    await client.run_until_disconnected()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        logging.info("Bot stopped.")

    except Exception as e:

        logging.exception(
            f"Fatal error: {e}"
) 
