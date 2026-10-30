"""Lokale Embeddings (CPU) für die Kandidatensuche.

Standard ist intfloat/multilingual-e5-small (mehrsprachig, 384 Dimensionen, läuft gut auf der
CPU). Alternativ z. B. sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2. Das Modell
wird beim ersten Aufruf von Hugging Face geladen. Vektoren werden je Modell unter
data/cache/embeddings/ zwischengespeichert; ein erneuter Lauf rechnet nur neue Texte.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Callable

import numpy as np

from ..paths import DATA_DIR

DEFAULT_EMBEDDING_MODEL = "intfloat/multilingual-e5-small"
CACHE_DIR = DATA_DIR / "cache" / "embeddings"


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:20]


class Embedder:
    """encode(texts, art) liefert normierte Vektoren. art ist "anfrage" (das zu ordnende Objekt)
    oder "ziel" (Vokabularbegriff); E5-Modelle brauchen dafür die Präfixe query:/passage:."""

    def __init__(self, model: str = DEFAULT_EMBEDDING_MODEL, backend: Callable[[list[str]], np.ndarray] | None = None,
                 cache_dir: Path = CACHE_DIR, batch: int = 64):
        self.model = model
        self.batch = batch
        self._backend = backend
        self.cache_path = Path(cache_dir) / (re.sub(r"[^\w.-]+", "_", model) + ".npz")
        self._cache: dict[str, np.ndarray] | None = None

    def _encode_raw(self, texts: list[str]) -> np.ndarray:
        if self._backend is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise RuntimeError("Für lokale Embeddings: pip install sentence-transformers") from e
            st = SentenceTransformer(self.model, device="cpu")
            self._backend = lambda t: st.encode(t, batch_size=self.batch, normalize_embeddings=True,
                                                show_progress_bar=False)
        return np.asarray(self._backend(texts), dtype=np.float32)

    def _load(self) -> dict[str, np.ndarray]:
        if self._cache is None:
            self._cache = {}
            if self.cache_path.exists():
                with np.load(self.cache_path) as z:
                    self._cache = {k: z[k] for k in z.files}
        return self._cache

    def encode(self, texts: list[str], art: str = "ziel", on_progress: Callable[[int, int], None] | None = None) -> np.ndarray:
        if "e5" in self.model.lower():
            texts = [("query: " if art == "anfrage" else "passage: ") + t for t in texts]
        cache = self._load()
        keys = [_h(t) for t in texts]
        todo = [i for i, k in enumerate(keys) if k not in cache]
        for start in range(0, len(todo), self.batch * 8):
            part = todo[start:start + self.batch * 8]
            vecs = self._encode_raw([texts[i] for i in part])
            for i, v in zip(part, vecs):
                cache[keys[i]] = v / (np.linalg.norm(v) or 1.0)
            self._save()
            if on_progress:
                on_progress(min(start + len(part), len(todo)), len(todo))
        return np.stack([cache[k] for k in keys]) if keys else np.zeros((0, 1), dtype=np.float32)

    def _save(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_name(self.cache_path.stem + ".tmp.npz")
        np.savez(tmp, **self._cache)
        tmp.replace(self.cache_path)


def top_k(anfragen: np.ndarray, ziele: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Je Anfrage die k ähnlichsten Ziele (Indizes, Kosinus-Ähnlichkeiten), absteigend."""
    sims = anfragen @ ziele.T
    k = min(k, ziele.shape[0])
    idx = np.argsort(-sims, axis=1)[:, :k]
    return idx, np.take_along_axis(sims, idx, axis=1)
