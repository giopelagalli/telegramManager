from __future__ import annotations

import logging

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from bot.telegram.commands import COMMANDS
from bot.telegram.handlers import Handlers

logger = logging.getLogger(__name__)

ERROR_REPLY = "Something went wrong on my side. If that was a message, send it again."


def build_application(settings, router, sender, transcriber=None) -> Application:
    handlers = Handlers(router, sender, transcriber, settings.data_dir / "tmp")
    only_me = filters.User(settings.telegram_user_id)

    async def post_init(app: Application) -> None:
        await app.bot.set_my_commands([(name, desc) for name, desc, menu in COMMANDS if menu])
        logger.info("listening as @%s, answering only user id %s", app.bot.username, settings.telegram_user_id)

    async def on_stranger(update, context) -> None:
        user = update.effective_user
        logger.warning("ignored a message from user id %s (not %s)", getattr(user, "id", None), settings.telegram_user_id)

    async def on_callback(update, context) -> None:
        user = update.effective_user
        if user is not None and user.id == settings.telegram_user_id:
            await handlers.on_callback(update, context)

    async def on_error(update, context) -> None:
        logger.exception("update handling failed", exc_info=context.error)
        user = getattr(update, "effective_user", None)
        chat = getattr(update, "effective_chat", None)
        if user is None or chat is None or user.id != settings.telegram_user_id:
            return
        # Answer where it broke: a group topic, not the DM.
        message = getattr(update, "effective_message", None)
        thread_id = getattr(message, "message_thread_id", None) if message else None
        thread = {} if thread_id is None else {"message_thread_id": thread_id}
        await context.bot.send_message(chat.id, ERROR_REPLY, **thread)

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .media_write_timeout(120)  # voice notes upload from home wifi; the default 20s cut long ones off
        .post_init(post_init)
        .build()
    )
    for name, _description, _menu in COMMANDS:
        app.add_handler(CommandHandler(name, handlers.command(name), filters=only_me))
    app.add_handler(MessageHandler(only_me & filters.TEXT & ~filters.COMMAND, handlers.on_text))
    app.add_handler(MessageHandler(only_me & filters.VOICE, handlers.on_voice))
    app.add_handler(MessageHandler(only_me & filters.PHOTO, handlers.on_photo))
    app.add_handler(MessageHandler(only_me & filters.Document.ALL, handlers.on_document))
    app.add_handler(MessageHandler(only_me & filters.LOCATION, handlers.on_location))
    app.add_handler(
        MessageHandler(only_me & filters.StatusUpdate.FORUM_TOPIC_CREATED, handlers.on_forum_topic_created)
    )
    app.add_handler(
        MessageHandler(only_me & filters.StatusUpdate.FORUM_TOPIC_EDITED, handlers.on_forum_topic_edited)
    )
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(~only_me, on_stranger))
    app.add_error_handler(on_error)
    return app
