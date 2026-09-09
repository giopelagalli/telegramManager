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
        await app.bot.set_my_commands(COMMANDS)

    async def on_callback(update, context) -> None:
        user = update.effective_user
        if user is not None and user.id == settings.telegram_user_id:
            await handlers.on_callback(update, context)

    async def on_error(update, context) -> None:
        logger.exception("update handling failed", exc_info=context.error)
        chat = getattr(update, "effective_chat", None)
        if chat is not None and chat.id == settings.telegram_user_id:
            await context.bot.send_message(chat.id, ERROR_REPLY)

    app = (
        Application.builder().token(settings.telegram_bot_token).post_init(post_init).build()
    )
    for name, _description in COMMANDS:
        app.add_handler(CommandHandler(name, handlers.command(name), filters=only_me))
    app.add_handler(MessageHandler(only_me & filters.TEXT & ~filters.COMMAND, handlers.on_text))
    app.add_handler(MessageHandler(only_me & filters.VOICE, handlers.on_voice))
    app.add_handler(MessageHandler(only_me & filters.PHOTO, handlers.on_photo))
    app.add_handler(MessageHandler(only_me & filters.Document.ALL, handlers.on_document))
    app.add_handler(MessageHandler(only_me & filters.LOCATION, handlers.on_location))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(on_error)
    return app
