"""A small local vector index over everything Luna has kept: memories, log lines, sources.

Vectors come from an OpenAI-compatible /embeddings endpoint (Fireworks on the same key). The
index is one JSON file in data/; only new or changed texts are embedded, keyed by content hash.
Fine into the tens of thousands of entries; swap the storage for npy/faiss past that."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


class Embedder:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0):
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self._model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):
            resp = await self._client.embeddings.create(model=self._model, input=texts[i : i + 64])
            out += [d.embedding for d in resp.data]
        return out


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class VectorIndex:
    def __init__(self, path: Path, embedder):
        self.path = Path(path)
        self.embedder = embedder
        self._ids: list[str] = []
        self._texts: list[str] = []
        self._vecs: np.ndarray | None = None
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._ids, self._texts = data["ids"], data["texts"]
            self._vecs = np.array(data["vecs"], dtype=np.float32) if data["vecs"] else None
        except (ValueError, KeyError):
            logger.warning("vector index unreadable, starting fresh")

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "ids": self._ids, "texts": self._texts,
            "vecs": self._vecs.tolist() if self._vecs is not None else [],
        }), encoding="utf-8")

    async def sync(self, texts: list[str]) -> int:
        """Embed whatever is new, drop what is gone. Returns how many were embedded."""
        wanted = {_key(t): t for t in texts if t.strip()}
        have = set(self._ids)
        new_keys = [k for k in wanted if k not in have]
        gone = {i for i, k in enumerate(self._ids) if k not in wanted}
        if gone:
            keep = [i for i in range(len(self._ids)) if i not in gone]
            self._ids = [self._ids[i] for i in keep]
            self._texts = [self._texts[i] for i in keep]
            self._vecs = self._vecs[keep] if self._vecs is not None and keep else None
        if new_keys:
            vecs = await self.embedder.embed([wanted[k] for k in new_keys])
            arr = np.array(vecs, dtype=np.float32)
            arr /= np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9
            self._vecs = arr if self._vecs is None else np.vstack([self._vecs, arr])
            self._ids += new_keys
            self._texts += [wanted[k] for k in new_keys]
        if new_keys or gone:
            self._save()
        return len(new_keys)

    async def search(self, query: str, k: int = 8) -> list[tuple[float, str]]:
        if self._vecs is None or not len(self._vecs):
            return []
        q = np.array((await self.embedder.embed([query]))[0], dtype=np.float32)
        q /= np.linalg.norm(q) + 1e-9
        scores = self._vecs @ q
        top = np.argsort(-scores)[:k]
        return [(float(scores[i]), self._texts[i]) for i in top if scores[i] > 0.2]
