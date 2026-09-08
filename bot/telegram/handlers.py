from __future__ import annotations

from pathlib import Path

from bot.knowledge.views import esc

LOW_CONFIDENCE = -0.8


class Handlers:
    """Unwraps telegram updates, calls the router, sends what comes back."""

    def __init__(self, router, sender, transcriber=None, tmp_dir: Path = Path(".")):
        self.router = router
        self.sender = sender
        self.transcriber = transcriber
        self.tmp_dir = Path(tmp_dir)

    async def _send(self, outs) -> None:
        for out in outs:
            await self.sender.send(out)

    def command(self, name: str):
        async def handler(update, context):
            arg = " ".join(context.args) if context.args else ""
            await self._send(await self.router.command(name, arg))

        return handler

    async def on_text(self, update, context) -> None:
        await self._send(await self.router.on_text(update.effective_message.text))

    async def on_voice(self, update, context) -> None:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        voice = update.effective_message.voice
        path = self.tmp_dir / f"{voice.file_id}.ogg"
        file = await voice.get_file()
        await file.download_to_drive(path)
        try:
            text, confidence = await self.transcriber.transcribe(path)
        finally:
            path.unlink(missing_ok=True)
        outs = await self.router.on_text(text, via_voice=True)
        if outs and confidence < LOW_CONFIDENCE:
            outs[0].text = f"Heard: “{esc(text)}”\n" + outs[0].text
        await self._send(outs)

    async def on_photo(self, update, context) -> None:
        file = await update.effective_message.photo[-1].get_file()
        image = await file.download_as_bytearray()
        await self._send(await self.router.on_photo(bytes(image)))

    async def on_location(self, update, context) -> None:
        location = update.effective_message.location
        await self._send(await self.router.on_location(location.latitude, location.longitude))

    async def on_callback(self, update, context) -> None:
        query = update.callback_query
        await query.answer()
        await self._send(await self.router.on_callback(query.data))
