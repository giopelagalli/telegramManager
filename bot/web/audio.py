"""Reply audio for the web: JD's voice notes (OGG/Opus) as AAC in .m4a, which every browser plays,
iPhone Safari included. Kept on disk for a bounded count and age, so a restart still serves them."""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

MIME = "audio/mp4"
KEEP_COUNT = 50
KEEP_SECONDS = 24 * 3600
_ID_RE = re.compile(r"^[0-9a-f]{32}$")


async def to_m4a(src: Path, dst: Path) -> None:
    """ffmpeg's built-in AAC encoder: present in every ffmpeg build, unlike libmp3lame."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-c:a", "aac", "-b:a", "64k",
        "-movflags", "+faststart", str(dst),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        tail = stderr[-2000:].decode(errors="replace") if stderr else ""
        raise RuntimeError(f"ffmpeg failed: {tail}")


class AudioStore:
    def __init__(
        self,
        directory: Path,
        transcode: Callable[[Path, Path], Awaitable[None]] = to_m4a,
        keep_count: int = KEEP_COUNT,
        keep_seconds: int = KEEP_SECONDS,
    ):
        self.directory = Path(directory)
        self.transcode = transcode
        self.keep_count = keep_count
        self.keep_seconds = keep_seconds

    async def add(self, voice_note: Path) -> str:
        """Store a voice note as m4a; returns its id. The source file is left to the caller."""
        self.directory.mkdir(parents=True, exist_ok=True)
        audio_id = uuid.uuid4().hex
        await self.transcode(Path(voice_note), self.directory / f"{audio_id}.m4a")
        self._prune()
        return audio_id

    def path(self, audio_id: str) -> Path | None:
        if not _ID_RE.match(audio_id):
            return None
        path = self.directory / f"{audio_id}.m4a"
        return path if path.is_file() else None

    def _prune(self) -> None:
        files = sorted(self.directory.glob("*.m4a"), key=lambda p: p.stat().st_mtime, reverse=True)
        cutoff = time.time() - self.keep_seconds
        for i, path in enumerate(files):
            if i >= self.keep_count or path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
