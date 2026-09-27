import os
import re
import sqlite3
import logging
import asyncio
from typing import Optional

from dotenv import load_dotenv

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import (
    MessageEntityCustomEmoji,
)
from telethon.errors import FloodWaitError


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

OWNER_ID = int(os.getenv("OWNER_ID", "0"))

SOURCE_CHAT_ID = int(
    os.getenv("SOURCE_CHAT_ID", "0")
)

DB_FILE = os.getenv(
    "DB_FILE",
    "bot.db"
)

USER_SESSION = os.getenv(
    "USER_SESSION",
    ""
)

# Users allowed to manage the template
TEMPLATE_ADMINS = {
    int(x.strip())
    for x in os.getenv(
        "TEMPLATE_ADMINS",
        ""
    ).split(",")
    if x.strip()
}

# Optional normal admins
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv(
        "ADMIN_IDS",
        ""
    ).split(",")
    if x.strip()
}

# Target chats
TARGET_CHAT_IDS = [
    int(x.strip())
    for x in os.getenv(
        "TARGET_CHAT_IDS",
        ""
    ).split(",")
    if x.strip()
]


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger(__name__)


# ============================================================
# CLIENTS
# ============================================================

bot_client = TelegramClient(
    "bot_session",
    API_ID,
    API_HASH
)

user_client = TelegramClient(
    StringSession(USER_SESSION),
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
CREATE TABLE IF NOT EXISTS published_messages (
    source_msg_id INTEGER,
    target_chat_id INTEGER,
    target_msg_id INTEGER,
    PRIMARY KEY (
        source_msg_id,
        target_chat_id
    )
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS template_data (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    source_msg_id INTEGER NOT NULL,
    template_text TEXT NOT NULL
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS template_emojis (
    source_msg_id INTEGER NOT NULL,
    entity_offset INTEGER NOT NULL,
    entity_length INTEGER NOT NULL,
    custom_emoji_id INTEGER NOT NULL,
    PRIMARY KEY (
        source_msg_id,
        entity_offset,
        entity_length
    )
)
""")

db.commit()


# ============================================================
# DATABASE HELPERS
# ============================================================

def save_published(
    source_msg_id: int,
    target_chat_id: int,
    target_msg_id: int
):
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


def get_published(
    source_msg_id: int
):
    return db.execute(
        """
        SELECT target_chat_id, target_msg_id
        FROM published_messages
        WHERE source_msg_id = ?
        """,
        (source_msg_id,)
    ).fetchall()


def delete_published(
    source_msg_id: int
):
    db.execute(
        """
        DELETE FROM published_messages
        WHERE source_msg_id = ?
        """,
        (source_msg_id,)
    )

    db.commit()


# ============================================================
# UTF-16 HELPERS
# Telegram offsets/lengths are UTF-16 based
# ============================================================

def utf16_len(text: str) -> int:
    return len(
        text.encode(
            "utf-16-le"
        )
    ) // 2


def utf16_to_py_index(
    text: str,
    utf16_offset: int
) -> int:

    current = 0

    for i, char in enumerate(text):

        size = utf16_len(char)

        if current >= utf16_offset:
            return i

        current += size

    return len(text)


def entity_text(
    text: str,
    entity
) -> str:

    start = utf16_to_py_index(
        text,
        entity.offset
    )

    end = utf16_to_py_index(
        text,
        entity.offset + entity.length
    )

    return text[start:end]


# ============================================================
# TEMPLATE
# ============================================================

async def save_template(
    message
) -> bool:

    text = message.raw_text or ""

    if not text:
        log.warning(
            "Template message has no text."
        )
        return False

    custom_emojis = []

    for entity in message.entities or []:

        if isinstance(
            entity,
            MessageEntityCustomEmoji
        ):

            custom_emojis.append(
                entity
            )

    if not custom_emojis:

        log.warning(
            "Template message %s has NO custom emoji.",
            message.id
        )

        return False

    # Remove old template
    db.execute(
        "DELETE FROM template_emojis"
    )

    db.execute(
        "DELETE FROM template_data"
    )

    # Save main template
    db.execute(
        """
        INSERT INTO template_data
        (
            id,
            source_msg_id,
            template_text
        )
        VALUES (1, ?, ?)
        """,
        (
            message.id,
            text
        )
    )

    # Save every Premium emoji
    for entity in custom_emojis:

        db.execute(
            """
            INSERT INTO template_emojis
            (
                source_msg_id,
                entity_offset,
                entity_length,
                custom_emoji_id
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                message.id,
                entity.offset,
                entity.length,
                entity.document_id
            )
        )

    db.commit()

    log.info(
        "TEMPLATE SAVED"
    )

    log.info(
        "Message ID: %s",
        message.id
    )

    log.info(
        "Text: %r",
        text
    )

    log.info(
        "Custom emojis: %s",
        len(custom_emojis)
    )

    return True


def get_template():

    row = db.execute(
        """
        SELECT source_msg_id, template_text
        FROM template_data
        WHERE id = 1
        """
    ).fetchone()

    if not row:
        return None

    source_msg_id, template_text = row

    rows = db.execute(
        """
        SELECT
            entity_offset,
            entity_length,
            custom_emoji_id
        FROM template_emojis
        WHERE source_msg_id = ?
        ORDER BY entity_offset ASC
        """,
        (source_msg_id,)
    ).fetchall()

    emojis = []

    for (
        offset,
        length,
        custom_emoji_id
    ) in rows:

        emojis.append(
            {
                "offset": offset,
                "length": length,
                "custom_emoji_id":
                    custom_emoji_id
            }
        )

    return {
        "source_msg_id": source_msg_id,
        "text": template_text,
        "emojis": emojis
    }


# ============================================================
# TEMPLATE EMOJI MAP
# ============================================================

def build_template_emoji_map(
    template
):

    result = []

    text = template["text"]

    for item in template["emojis"]:

        start = utf16_to_py_index(
            text,
            item["offset"]
        )

        end = utf16_to_py_index(
            text,
            item["offset"] +
            item["length"]
        )

        visible = text[start:end]

        result.append(
            {
                "visible": visible,
                "custom_emoji_id":
                    item["custom_emoji_id"],
                "template_offset":
                    item["offset"],
                "template_length":
                    item["length"]
            }
        )

    return result


# ============================================================
# FIND VISIBLE EMOJIS IN FRIEND MESSAGE
# ============================================================

def find_occurrences(
    text: str,
    value: str,
    start_from: int = 0
):

    results = []

    if not value:
        return results

    position = start_from

    while True:

        index = text.find(
            value,
            position
        )

        if index == -1:
            break

        results.append(index)

        position = (
            index + len(value)
        )

    return results


# ============================================================
# APPLY TEMPLATE EMOJIS
# ============================================================

def apply_template(
    new_text: str,
    template
):

    if not new_text:
        return new_text, []

    emoji_map = build_template_emoji_map(
        template
    )

    if not emoji_map:
        return new_text, []

    entities = []

    search_position = 0

    for emoji in emoji_map:

        visible = emoji["visible"]

        occurrences = find_occurrences(
            new_text,
            visible,
            search_position
        )

        if not occurrences:
            log.warning(
                "Template emoji %r not found "
                "in new message.",
                visible
            )

            continue

        py_index = occurrences[0]

        # Continue searching after this emoji
        search_position = (
            py_index + len(visible)
        )

        utf16_offset = utf16_len(
            new_text[:py_index]
        )

        utf16_length = utf16_len(
            visible
        )

        entities.append(
            MessageEntityCustomEmoji(
                offset=utf16_offset,
                length=utf16_length,
                document_id=
                    emoji["custom_emoji_id"]
            )
        )

    return new_text, entities


# ============================================================
# CHECK IF MESSAGE ALREADY HAS CUSTOM EMOJI
# ============================================================

def has_custom_emoji(
    message
) -> bool:

    for entity in message.entities or []:

        if isinstance(
            entity,
            MessageEntityCustomEmoji
        ):
            return True

    return False


# ============================================================
# SEND WITH PREMIUM USER
# ============================================================

async def send_with_send_as(
    target_chat_id,
    text,
    entities=None,
    file=None,
    reply_to=None
):

    try:

        sent = await user_client.send_message(
            target_chat_id,
            text,
            formatting_entities=entities,
            file=file,
            reply_to=reply_to,
            send_as=target_chat_id
        )

        return sent

    except FloodWaitError as e:

        log.warning(
            "FloodWait: %s seconds",
            e.seconds
        )

        await asyncio.sleep(
            e.seconds
        )

        return await user_client.send_message(
            target_chat_id,
            text,
            formatting_entities=entities,
            file=file,
            reply_to=reply_to,
            send_as=target_chat_id
        )

    except Exception:

        log.exception(
            "send_as failed for %s",
            target_chat_id
        )

        return None


# ============================================================
# PUBLISH
# ============================================================

async def publish_message(
    message
):

    # --------------------------------------------------------
    # Ignore commands/messages beginning with "."
    # --------------------------------------------------------

    raw_text = message.raw_text or ""

    if raw_text.startswith("."):

        log.info(
            "Message %s starts with '.', "
            "not publishing.",
            message.id
        )

        return


    sender_id = message.sender_id

    log.info(
        "NEW SOURCE MESSAGE"
    )

    log.info(
        "Message ID: %s",
        message.id
    )

    log.info(
        "Sender ID: %s",
        sender_id
    )

    log.info(
        "Text: %r",
        raw_text
    )


    # --------------------------------------------------------
    # Get current template
    # --------------------------------------------------------

    template = get_template()

    final_text = raw_text
    final_entities = list(
        message.entities or []
    )


    # --------------------------------------------------------
    # If user already sent custom emoji,
    # keep them.
    #
    # Otherwise apply template.
    # --------------------------------------------------------

    if not has_custom_emoji(message):

        if template:

            # Template admins are allowed
            # to publish their own custom emoji.
            if sender_id not in TEMPLATE_ADMINS:

                (
                    final_text,
                    template_entities
                ) = apply_template(
                    raw_text,
                    template
                )

                final_entities = (
                    template_entities
                )

                log.info(
                    "Template applied to "
                    "message %s",
                    message.id
                )

    else:

        log.info(
            "Message %s already has "
            "custom emoji.",
            message.id
        )


    # --------------------------------------------------------
    # Media
    # --------------------------------------------------------

    file = None

    if message.media:

        try:

            file = await message.download_media()

        except Exception:

            log.exception(
                "Could not download media "
                "from message %s",
                message.id
            )


    # --------------------------------------------------------
    # Reply source
    # --------------------------------------------------------

    reply_to_source = None

    if message.reply_to_msg_id:

        reply_to_source = (
            message.reply_to_msg_id
        )


    # --------------------------------------------------------
    # Publish to all targets
    # --------------------------------------------------------

    for target_chat_id in TARGET_CHAT_IDS:

        try:

            reply_to_target = None

            if reply_to_source:

                rows = get_published(
                    reply_to_source
                )

                for (
                    saved_chat_id,
                    saved_msg_id
                ) in rows:

                    if (
                        saved_chat_id
                        == target_chat_id
                    ):

                        reply_to_target = (
                            saved_msg_id
                        )

                        break


            sent = await send_with_send_as(
                target_chat_id=
                    target_chat_id,
                text=final_text,
                entities=final_entities,
                file=file,
                reply_to=reply_to_target
            )


            # ------------------------------------------------
            # Bot fallback
            # ------------------------------------------------

            if not sent:

                try:

                    sent = await bot_client.send_message(
                        target_chat_id,
                        final_text,
                        formatting_entities=
                            final_entities,
                        file=file,
                        reply_to=
                            reply_to_target
                    )

                except Exception:

                    log.exception(
                        "Bot fallback failed "
                        "for %s",
                        target_chat_id
                    )

                    continue


            if sent:

                save_published(
                    source_msg_id=
                        message.id,
                    target_chat_id=
                        target_chat_id,
                    target_msg_id=
                        sent.id
                )

                log.info(
                    "Published %s -> %s "
                    "message=%s",
                    message.id,
                    target_chat_id,
                    sent.id
                )

        except Exception:

            log.exception(
                "Publish failed "
                "target=%s",
                target_chat_id
            )


    # --------------------------------------------------------
    # Cleanup downloaded media
    # --------------------------------------------------------

    if file:

        try:

            if os.path.isfile(file):

                os.remove(file)

        except Exception:

            pass


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

        # Ignore service messages
        if not event.message:
            return

        await publish_message(
            event.message
        )

    except Exception:

        log.exception(
            "SOURCE NEW MESSAGE ERROR"
        )


# ============================================================
# PIN DETECTION
#
# IMPORTANT:
# ChatAction is the correct Telethon event
# for detecting a new pin.
# ============================================================

@bot_client.on(
    events.ChatAction(
        chats=SOURCE_CHAT_ID
    )
)
async def template_pin_handler(event):

    try:

        if not event.new_pin:
            return


        # ----------------------------------------------------
        # Who pinned it?
        # ----------------------------------------------------

        pinner_id = None

        try:

            pinner_id = event.user_id

        except Exception:

            pass


        log.info(
            "=========================================="
        )

        log.info(
            "PIN DETECTED -> pinner=%s",
            pinner_id
        )


        # ----------------------------------------------------
        # Only TEMPLATE_ADMINS can change template
        # ----------------------------------------------------

        if (
            pinner_id is None
            or
            pinner_id not in TEMPLATE_ADMINS
        ):

            log.info(
                "PIN IGNORED -> %s "
                "is not TEMPLATE_ADMIN",
                pinner_id
            )

            return


        # ----------------------------------------------------
        # Get the message that was pinned
        # ----------------------------------------------------

        pinned = await event.get_pinned_message()


        if not pinned:

            log.warning(
                "PIN DETECTED BUT "
                "MESSAGE NOT FOUND"
            )

            return


        log.info(
            "PINNED MESSAGE FOUND -> id=%s",
            pinned.id
        )


        # ----------------------------------------------------
        # Save as template
        # ----------------------------------------------------

        saved = await save_template(
            pinned
        )


        if saved:

            log.info(
                "=========================================="
            )

            log.info(
                "TEMPLATE UPDATED AUTOMATICALLY"
            )

            log.info(
                "Template message: %s",
                pinned.id
            )

            log.info(
                "Pinned by: %s",
                pinner_id
            )

            log.info(
                "=========================================="
            )

        else:

            log.warning(
                "Pinned message has no "
                "custom emoji."
            )

    except Exception:

        log.exception(
            "TEMPLATE PIN HANDLER ERROR"
        )


# ============================================================
# EDIT SOURCE MESSAGE
# ============================================================

@bot_client.on(
    events.MessageEdited(
        chats=SOURCE_CHAT_ID
    )
)
async def source_message_edited(event):

    try:

        message = event.message

        rows = get_published(
            message.id
        )

        if not rows:
            return


        # ----------------------------------------------------
        # Rebuild text/entities
        # ----------------------------------------------------

        raw_text = message.raw_text or ""

        final_text = raw_text

        final_entities = list(
            message.entities or []
        )


        if not has_custom_emoji(message):

            template = get_template()

            if template:

                (
                    final_text,
                    final_entities
                ) = apply_template(
                    raw_text,
                    template
                )


        # ----------------------------------------------------
        # Download media if necessary
        # ----------------------------------------------------

        file = None

        if message.media:

            try:

                file = await message.download_media()

            except Exception:

                log.exception(
                    "Failed to download edited media"
                )


        # ----------------------------------------------------
        # Edit every published copy
        # ----------------------------------------------------

        for (
            target_chat_id,
            target_msg_id
        ) in rows:

            try:

                await user_client.edit_message(
                    target_chat_id,
                    target_msg_id,
                    final_text,
                    formatting_entities=
                        final_entities,
                    file=file
                )

            except Exception:

                log.exception(
                    "Failed editing "
                    "target=%s msg=%s",
                    target_chat_id,
                    target_msg_id
                )


        if file:

            try:

                if os.path.isfile(file):

                    os.remove(file)

            except Exception:

                pass

    except Exception:

        log.exception(
            "SOURCE EDIT ERROR"
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

            rows = get_published(
                source_msg_id
            )

            for (
                target_chat_id,
                target_msg_id
            ) in rows:

                try:

                    await user_client.delete_messages(
                        target_chat_id,
                        target_msg_id
                    )

                except Exception:

                    log.exception(
                        "Failed deleting "
                        "target=%s msg=%s",
                        target_chat_id,
                        target_msg_id
                    )

            delete_published(
                source_msg_id
            )

    except Exception:

        log.exception(
            "SOURCE DELETE ERROR"
        )


# ============================================================
# COMMAND: /id
# ============================================================

@bot_client.on(
    events.NewMessage(
        pattern=r"^/id$"
    )
)
async def command_id(event):

    await event.reply(
        f"Chat ID: {event.chat_id}\n"
        f"Your ID: {event.sender_id}"
    )


# ============================================================
# COMMAND: /status
# ============================================================

@bot_client.on(
    events.NewMessage(
        pattern=r"^/status$"
    )
)
async def command_status(event):

    template = get_template()

    if not template:

        await event.reply(
            "❌ No template is currently saved."
        )

        return


    emoji_count = len(
        template["emojis"]
    )


    await event.reply(
        "✅ Template active\n\n"
        f"Message ID: "
        f"{template['source_msg_id']}\n"
        f"Premium emojis: "
        f"{emoji_count}\n\n"
        "Pin a new message to replace it."
    )


# ============================================================
# COMMAND: /del
# ============================================================

@bot_client.on(
    events.NewMessage(
        pattern=r"^/del$"
    )
)
async def command_delete(event):

    if (
        event.sender_id != OWNER_ID
        and
        event.sender_id not in ADMIN_IDS
    ):

        return


    if not event.is_reply:

        await event.reply(
            "Reply to a source message "
            "with /del"
        )

        return


    replied = await event.get_reply_message()

    if not replied:

        return


    rows = get_published(
        replied.id
    )


    for (
        target_chat_id,
        target_msg_id
    ) in rows:

        try:

            await user_client.delete_messages(
                target_chat_id,
                target_msg_id
            )

        except Exception:

            pass


    delete_published(
        replied.id
    )


    await event.reply(
        "✅ Published copies deleted."
    )


# ============================================================
# COMMAND: /help
# ============================================================

@bot_client.on(
    events.NewMessage(
        pattern=r"^/help$"
    )
)
async def command_help(event):

    await event.reply(
        "📌 Bot commands:\n\n"
        "/id - show chat/user ID\n"
        "/status - show current template\n"
        "/del - delete published copies\n"
        "/help - show this message\n\n"
        "📌 Template:\n"
        "Pin a message in the source group "
        "using an allowed TEMPLATE_ADMIN.\n"
        "That message becomes the template "
        "automatically.\n\n"
        "Messages beginning with . "
        "stay only in the source group."
    )


# ============================================================
# STARTUP
# ============================================================

async def main():

    log.info(
        "=========================================="
    )

    log.info(
        "Starting Telegram Publisher..."
    )

    log.info(
        "SOURCE_CHAT_ID = %s",
        SOURCE_CHAT_ID
    )

    log.info(
        "TARGET_CHAT_IDS = %s",
        TARGET_CHAT_IDS
    )

    log.info(
        "TEMPLATE_ADMINS = %s",
        TEMPLATE_ADMINS
    )

    log.info(
        "=========================================="
    )


    # --------------------------------------------------------
    # Start bot
    # --------------------------------------------------------

    await bot_client.start(
        bot_token=BOT_TOKEN
    )


    # --------------------------------------------------------
    # Start Premium user
    # --------------------------------------------------------

    if not USER_SESSION:

        raise RuntimeError(
            "USER_SESSION is empty."
        )


    await user_client.start()


    # --------------------------------------------------------
    # Get identities
    # --------------------------------------------------------

    bot_me = await bot_client.get_me()

    user_me = await user_client.get_me()


    log.info(
        "BOT LOGIN: @%s (%s)",
        getattr(
            bot_me,
            "username",
            None
        ),
        bot_me.id
    )

    log.info(
        "USER LOGIN: @%s (%s)",
        getattr(
            user_me,
            "username",
            None
        ),
        user_me.id
    )


    # --------------------------------------------------------
    # Check source
    # --------------------------------------------------------

    try:

        source = await bot_client.get_entity(
            SOURCE_CHAT_ID
        )

        log.info(
            "SOURCE RESOLVED: %s",
            getattr(
                source,
                "title",
                SOURCE_CHAT_ID
            )
        )

    except Exception:

        log.exception(
            "Could not resolve SOURCE_CHAT_ID"
        )


    # --------------------------------------------------------
    # Show current template
    # --------------------------------------------------------

    template = get_template()

    if template:

        log.info(
            "Existing template loaded."
        )

        log.info(
            "Template message ID: %s",
            template["source_msg_id"]
        )

        log.info(
            "Template emoji count: %s",
            len(template["emojis"])
        )

    else:

        log.info(
            "No template saved yet."
        )

        log.info(
            "Pin a message in SOURCE_CHAT_ID "
            "using a TEMPLATE_ADMIN."
        )


    log.info(
        "BOT IS RUNNING."
    )


    await asyncio.gather(
        bot_client.run_until_disconnected(),
        user_client.run_until_disconnected()
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        log.info(
            "Stopped."
        )

    except Exception:

        log.exception(
            "FATAL ERROR"
    ) 
