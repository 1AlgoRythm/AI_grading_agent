"""[P1] Textbook retrieval helpers.

This module supports two retrieval paths:

- an optional vector-store-backed index over `textbook/` files when Chroma is
  available, and
- a deterministic fallback scorer based on word overlap and heading matches.

The vector-store path uses a local hash-based embedding function, so it stays
dependency-light and offline-friendly. If Chroma is unavailable, the fallback
path keeps the walking skeleton runnable without changing the public API.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from lanes.p1_storage import P1Store

__all__ = ["retrieve_method_from_textbook", "sync_textbook_index"]

def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _chunk_text(text: str, chunk_size: int = 900, overlap: int = 120) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return [chunk for chunk in chunks if chunk]


def _hash_embedding(text: str, dimensions: int = 128) -> list[float]:
    vector = [0.0] * dimensions
    tokens = re.findall(r"\w+", text.lower())
    if not tokens:
        return vector

    for token in tokens:
        digest = hashlib.md5(token.encode("utf8")).hexdigest()
        index = int(digest[:8], 16) % dimensions
        vector[index] += 1.0

    norm = sum(value * value for value in vector) ** 0.5
    if norm:
        vector = [value / norm for value in vector]
    return vector


def _list_textbook_sources() -> list[tuple[Path, str]]:
    tb_dir = Path(os.getcwd()) / "textbook"
    if not tb_dir.is_dir():
        return []

    sources: list[tuple[Path, str]] = []
    for path in sorted(tb_dir.iterdir()):
        if path.suffix.lower() not in {".txt", ".md"}:
            continue
        try:
            sources.append((path, path.read_text(encoding="utf8")))
        except Exception:
            continue
    return sources


def _index_textbook_with_chroma() -> tuple[object | None, list[tuple[Path, str]]]:
    try:
        import chromadb
    except Exception:
        return None, _list_textbook_sources()

    sources = _list_textbook_sources()
    if not sources:
        return None, []

    persist_dir = Path(os.getcwd()) / ".p1_textbook_index"
    client = chromadb.PersistentClient(path=str(persist_dir))
    # No embedding_function is registered on the collection: chroma's
    # embedding-function protocol has changed across versions (it now
    # expects a `.name()`/config-conflict contract our simple hash function
    # doesn't implement). Supplying our own precomputed embeddings on every
    # upsert/query sidesteps that entirely and is version-stable.
    collection = client.get_or_create_collection(
        name="textbook",
        metadata={"lane": "p1", "index": "textbook"},
    )

    documents: list[str] = []
    metadatas: list[dict[str, str]] = []
    ids: list[str] = []
    embeddings: list[list[float]] = []
    for path, content in sources:
        for chunk_index, chunk in enumerate(_chunk_text(content), start=1):
            documents.append(chunk)
            metadatas.append({"path": str(path), "chunk": str(chunk_index)})
            ids.append(f"{path.name}:{chunk_index}")
            embeddings.append(_hash_embedding(chunk))

    if documents:
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)
    return collection, sources


def retrieve_method_from_textbook(problem_statement: str) -> Optional[str]:
    """Return a short method snippet from the best matching textbook file."""
    if not problem_statement:
        return None
    stmt_words = _tokenize(problem_statement)
    best_score = 0
    best_snippet: str | None = None

    collection, sources = _index_textbook_with_chroma()
    if collection is not None:
        try:
            query_embedding = _hash_embedding(problem_statement)
            result = collection.query(query_embeddings=[query_embedding], n_results=1)
            documents = result.get("documents", [[]])
            if documents and documents[0]:
                return documents[0][0].strip()
        except Exception:
            # Fall back to the deterministic scorer below.
            pass

    for path, content in sources:
        content_lower = content.lower()
        content_words = _tokenize(content_lower)
        overlap = stmt_words & content_words
        score = len(overlap)
        if not score:
            continue

        # Light heading bias: if the statement shares a word with a heading,
        # reward the match a bit so concise chapter notes rank higher.
        heading_bonus = 0
        for line in content_lower.splitlines()[:10]:
            if line.startswith("#") or len(line) < 80:
                if _tokenize(line) & stmt_words:
                    heading_bonus += 1
        score += heading_bonus

        if score > best_score:
            best_score = score
            best_snippet = content.strip()[:400]

    return best_snippet if best_score > 0 else None


def sync_textbook_index(store: "P1Store") -> int:
    """Persist the on-disk `textbook/` corpus into the DB-backed
    `textbook_index` table (plan §8). Independent of the retrieval hot path
    above -- this just makes sure the corpus being retrieved from is also
    durably recorded and queryable, regardless of whether Chroma happens to
    be installed. Returns the number of chunks indexed."""
    total = 0
    for path, content in _list_textbook_sources():
        chunks = _chunk_text(content)
        store.index_textbook_chunks(str(path), chunks)
        total += len(chunks)
    return total
