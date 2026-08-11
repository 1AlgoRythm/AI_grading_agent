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

__all__ = ["rehydrate_textbook_from_db", "retrieve_method_from_textbook", "sync_textbook_index"]

def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "for", "of", "in", "on", "to", "is", "are",
    "was", "were", "be", "been", "being", "with", "at", "by", "from", "this", "that",
    "it", "as", "use", "using",
}


def _significant_words(text: str) -> set[str]:
    """Tokens worth using as a relevance signal, i.e. `_tokenize` minus common
    stopwords and generic verbs ("use", "for", ...). Without this, two
    completely unrelated passages can look related just because they both
    happen to contain "for" or "the" -- exactly the false-positive that let
    an irrelevant textbook chunk pass the relevance gate below."""
    return _tokenize(text) - _STOPWORDS


def _chunk_text(text: str, chunk_size: int = 900, overlap: int = 120) -> list[str]:
    """Split into overlapping chunks, snapping each boundary to the nearest
    whitespace so a chunk never starts or ends mid-word.

    A raw character-count cut (the previous behavior) regularly split a word
    across two chunks -- e.g. "...expand squar" / "es, use..." -- and that
    garbled fragment is exactly what gets embedded verbatim into a rubric
    criterion's description or a solution-generation prompt. The start
    boundary matters too, not just the end: a retrieved chunk is often shown
    or embedded on its own, not read back-to-back with its neighbor, so a
    chunk beginning "ares, use (a+b)^2..." (the tail of "squares") is just as
    visibly garbled as one ending mid-word.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        if end < len(text):
            snapped = end
            while snapped > start + 1 and not text[snapped - 1].isspace():
                snapped -= 1
            if snapped > start + 1:
                end = snapped
            # else: no whitespace anywhere in this window (one token longer
            # than chunk_size) -- fall back to the hard cut rather than loop.
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        next_start = max(start + 1, end - overlap)
        snapped_start = next_start
        while snapped_start < end and not text[snapped_start - 1].isspace():
            snapped_start += 1
        # The loop above exits two different ways: it *found* a boundary
        # (text[snapped_start - 1] is whitespace, possibly right at `end`),
        # or it ran out of room without finding one (one token spanning the
        # whole window) -- `snapped_start < end` alone can't tell those
        # apart, since a boundary found exactly at `end` also fails that
        # check. Test the actual character, not just the position.
        start = snapped_start if text[snapped_start - 1].isspace() else next_start
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


_INDEX_CACHE: dict[str, object] = {}


def _corpus_fingerprint(sources: list[tuple[Path, str]]) -> str:
    """Cheap change-detector over the on-disk corpus (mtime + size per file)
    so re-indexing only happens when the corpus actually changed."""
    parts = []
    for path, _ in sources:
        try:
            stat = path.stat()
            parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            parts.append(f"{path}:missing")
    return hashlib.md5("|".join(parts).encode("utf8")).hexdigest()


def _index_textbook_with_chroma() -> tuple[object | None, list[tuple[Path, str]]]:
    try:
        import chromadb
    except Exception:
        return None, _list_textbook_sources()

    sources = _list_textbook_sources()
    if not sources:
        return None, []

    # Without this, every retrieve_method_from_textbook call re-read,
    # re-chunked, re-embedded, and re-upserted the ENTIRE corpus -- fine for
    # one small algebra.txt file, but a real (e.g. CLRS-sized) corpus turns
    # every "Develop solution" click into a many-second re-index. The
    # in-process cache handles repeated calls within one run; the on-disk
    # fingerprint stamp handles a fresh process (container restart) not
    # re-indexing an unchanged corpus. Editing a textbook file changes its
    # mtime/size, so both invalidate automatically.
    fingerprint = _corpus_fingerprint(sources)
    if _INDEX_CACHE.get("fingerprint") == fingerprint and _INDEX_CACHE.get("collection") is not None:
        return _INDEX_CACHE["collection"], sources

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

    stamp = persist_dir / ".fingerprint"
    already_indexed = stamp.exists() and stamp.read_text(encoding="utf8").strip() == fingerprint

    if not already_indexed:
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
        persist_dir.mkdir(parents=True, exist_ok=True)
        stamp.write_text(fingerprint, encoding="utf8")

    _INDEX_CACHE["fingerprint"] = fingerprint
    _INDEX_CACHE["collection"] = collection
    return collection, sources


def retrieve_method_from_textbook(problem_statement: str) -> Optional[str]:
    """Return a short method snippet from the best matching textbook file."""
    if not problem_statement:
        return None
    stmt_words = _significant_words(problem_statement)
    best_score = 0
    best_snippet: str | None = None

    collection, sources = _index_textbook_with_chroma()
    if collection is not None:
        try:
            query_embedding = _hash_embedding(problem_statement)
            result = collection.query(query_embeddings=[query_embedding], n_results=3)
            documents = result.get("documents", [[]])[0]
            metadatas = result.get("metadatas", [[]])[0] or [{}] * len(documents)
            # Chroma always returns up to n_results nearest neighbors, even
            # when none of them are actually relevant -- the hash-based
            # embedding is a coarse bag-of-words signal, so for a small
            # corpus "nearest available" is not the same as "relevant," and
            # an unrelated chunk got dragged along just to fill the quota
            # (it flowed straight into solutions/rubrics/feedback text).
            # Gate each candidate on real lexical overlap with the query
            # before including it.
            relevant = [
                (doc, meta) for doc, meta in zip(documents, metadatas)
                if _significant_words(doc) & stmt_words
            ]
            if relevant:
                # Source-labeled so it's visible which chunk(s) grounded the
                # rubric, not just an unattributed blob of retrieved text.
                parts = []
                for doc, meta in relevant:
                    source_name = Path(str(meta.get("path", "textbook"))).name
                    parts.append(f"[{source_name}]\n{doc.strip()}")
                return "\n\n".join(parts)
            # No candidate was actually relevant -- fall through to the
            # deterministic scorer below rather than returning nothing; it
            # scores against full file content, not just this chunk set.
        except Exception:
            # Fall back to the deterministic scorer below.
            pass

    for path, content in sources:
        # Score against the whole file (so a relevant file with the match
        # buried past the first 400 characters still wins), but return the
        # actual matching CHUNK, not always the file's opening bytes -- the
        # returned snippet used to be `content.strip()[:400]` regardless of
        # where the overlapping words occurred, so a multi-section file
        # (e.g. an unrelated intro followed by the actually-relevant
        # chapter) always handed back the unrelated intro.
        for chunk in _chunk_text(content):
            chunk_lower = chunk.lower()
            overlap = stmt_words & _significant_words(chunk_lower)
            score = len(overlap)
            if not score:
                continue

            # Light heading bias: if the statement shares a word with a
            # heading-like line near the top of this chunk, reward the
            # match a bit so concise chapter notes rank higher.
            heading_bonus = 0
            for line in chunk_lower.splitlines()[:10]:
                if line.startswith("#") or len(line) < 80:
                    if _tokenize(line) & stmt_words:
                        heading_bonus += 1
            score += heading_bonus

            if score > best_score:
                best_score = score
                best_snippet = chunk.strip()[:400]

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


def rehydrate_textbook_from_db(store: "P1Store") -> int:
    """Restore `textbook/` from the DB-backed `textbook_index` table when
    the filesystem copy is missing or empty -- e.g. a fresh container with
    no volume mounted for `textbook/`, after a restart. The DB copy already
    survives restarts (same database the rest of the app persists through)
    but `sync_textbook_index` only ever writes filesystem -> DB; nothing
    reads it back the other way without this, so retrieval silently found
    nothing even though the exact same content was sitting right there.

    A no-op, safe to call unconditionally on every render, whenever the
    filesystem already has content -- a live, possibly-newer on-disk corpus
    is never clobbered by a DB snapshot that could be stale. Reconstructs
    each source file by concatenating its chunks in order; `_chunk_text`'s
    overlap means the result isn't a byte-perfect restore, but retrieval
    only needs a faithful-enough corpus to score against, not an exact copy.
    Returns the number of files restored.
    """
    if _list_textbook_sources():
        return 0

    chunks_by_source: dict[str, list[str]] = {}
    for source_path, content in store.textbook_chunks():
        chunks_by_source.setdefault(source_path, []).append(content)
    if not chunks_by_source:
        return 0

    tb_dir = Path(os.getcwd()) / "textbook"
    tb_dir.mkdir(exist_ok=True)
    restored = 0
    for source_path, chunks in chunks_by_source.items():
        name = Path(source_path).name
        if not name:
            continue
        (tb_dir / name).write_text("\n".join(chunks), encoding="utf8")
        restored += 1
    return restored
