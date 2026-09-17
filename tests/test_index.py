import numpy as np
from bot.memory.index import VectorIndex

class FakeEmbedder:
    """Deterministic 8-dim vectors from character bigrams; similar strings land close."""
    def __init__(self): self.calls = 0
    async def embed(self, texts):
        self.calls += 1
        out = []
        for t in texts:
            v = np.zeros(8)
            for i in range(len(t) - 1):
                v[sum(map(ord, t[i:i+2].lower())) % 8] += 1
            out.append(v.tolist())
        return out

async def test_index_syncs_incrementally_and_searches(tmp_path):
    e = FakeEmbedder()
    idx = VectorIndex(tmp_path / "index.json", e)
    assert await idx.sync(["dentist friday at 2", "bio lab done", "sam is his lab partner"]) == 3
    assert await idx.sync(["dentist friday at 2", "bio lab done", "sam is his lab partner"]) == 0
    assert await idx.sync(["dentist friday at 2", "sam is his lab partner", "new: gym at 6"]) == 1
    hits = await idx.search("dentist friday at 2", k=2)
    assert hits and hits[0][1] == "dentist friday at 2"
    again = VectorIndex(tmp_path / "index.json", e)
    assert len(again._ids) == 3
