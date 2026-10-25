"""Module 4: embeddings with Sentence Transformers and a FAISS vector store.

Embeddings are L2-normalised, so the inner product FAISS computes is exactly the
cosine similarity between the question and each chunk.
"""
import json
from functools import lru_cache
from pathlib import Path

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

from document_loader import Chunk

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"   # 384-dimensional embeddings
INDEX_FILE = "index.faiss"
CHUNKS_FILE = "chunks.json"


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    return SentenceTransformer(MODEL_NAME, device="cpu")


def embed(texts: list[str]) -> np.ndarray:
    vectors = get_model().encode(texts, batch_size=32, normalize_embeddings=True,
                                 show_progress_bar=False)
    return np.asarray(vectors, dtype="float32")


class VectorStore:
    def __init__(self, index: faiss.Index, chunks: list[Chunk]):
        self.index = index
        self.chunks = chunks

    @classmethod
    def build(cls, chunks: list[Chunk]) -> "VectorStore":
        vectors = embed([c.text for c in chunks])
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)
        return cls(index, chunks)

    def search(self, query: str, k: int = 4) -> list[dict]:
        """Top-k chunks with their cosine similarity score, best first."""
        scores, ids = self.index.search(embed([query]), min(k, len(self.chunks)))
        return [
            {**self.chunks[i].to_dict(), "score": float(s)}
            for s, i in zip(scores[0], ids[0]) if i != -1
        ]

    @property
    def sources(self) -> list[str]:
        return sorted({c.source for c in self.chunks})

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(folder / INDEX_FILE))
        (folder / CHUNKS_FILE).write_text(
            json.dumps({"model": MODEL_NAME, "chunks": [c.to_dict() for c in self.chunks]}, indent=1),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, folder: Path) -> "VectorStore":
        index = faiss.read_index(str(folder / INDEX_FILE))
        data = json.loads((folder / CHUNKS_FILE).read_text(encoding="utf-8"))
        if data["model"] != MODEL_NAME:
            raise ValueError("Saved index was built with a different embedding model; rebuild it.")
        return cls(index, [Chunk(**c) for c in data["chunks"]])

    @staticmethod
    def exists(folder: Path) -> bool:
        return (folder / INDEX_FILE).exists() and (folder / CHUNKS_FILE).exists()
