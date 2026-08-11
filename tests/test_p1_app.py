"""Tests for p1_app.py helpers."""
from __future__ import annotations

from pathlib import Path

import p1_app


class _FakeUpload:
    def __init__(self, name: str, content: bytes) -> None:
        self.name = name
        self._content = content

    def getvalue(self) -> bytes:
        return self._content


class _FakeSessionState(dict):
    """Real st.session_state supports both dict-style (.get) and attribute
    (.foo = ...) access; a plain dict only supports the former, and
    _auto_sync_textbook_if_changed uses the latter."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value


def test_write_uploaded_file_preserves_the_original_filename():
    # NamedTemporaryFile randomizes the name (tmpXXXXXXXX), which then
    # became the assignment label / student handle via Path(source).stem --
    # meaningless in the UI. mkdtemp + the real filename fixes that.
    upload = _FakeUpload("hw3.pdf", b"Problem 1 (5 points): Solve for x.")

    path = p1_app._write_uploaded_file(upload)

    assert Path(path).name == "hw3.pdf"
    assert Path(path).read_bytes() == b"Problem 1 (5 points): Solve for x."


def test_write_uploaded_file_falls_back_when_no_name_given():
    upload = _FakeUpload("", b"content")
    path = p1_app._write_uploaded_file(upload)
    assert Path(path).name == "upload.txt"


def test_save_textbook_upload_writes_a_text_file_into_textbook_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    upload = _FakeUpload("greedy.txt", b"Exchange argument: replace the first interval.")

    dest = p1_app._save_textbook_upload(upload)

    assert dest.resolve() == (tmp_path / "textbook" / "greedy.txt").resolve()
    assert dest.read_bytes() == b"Exchange argument: replace the first interval."


def test_save_textbook_upload_extracts_pdf_text_to_markdown(tmp_path, monkeypatch):
    # _list_textbook_sources() only reads .txt/.md -- a PDF saved as-is would
    # be silently invisible to the retrieval corpus.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(p1_app, "_read_pdf_text", lambda path: "Extracted PDF text about substitution.")
    upload = _FakeUpload("calc.pdf", b"%PDF-1.4 fake bytes")

    dest = p1_app._save_textbook_upload(upload)

    assert dest.suffix == ".md"
    assert dest.resolve() == (tmp_path / "textbook" / "calc.md").resolve()
    assert "substitution" in dest.read_text().lower()


def test_auto_sync_only_runs_once_per_unchanged_corpus(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from lanes import p1_rag
    from lanes.p1_storage import P1Store

    p1_rag._INDEX_CACHE.clear()
    textbook = tmp_path / "textbook"
    textbook.mkdir()
    (textbook / "algebra.txt").write_text("To expand squares, use (a+b)^2.", encoding="utf8")
    store = P1Store(f"sqlite:///{tmp_path / 'p1.db'}")

    monkeypatch.setattr(p1_app.st, "session_state", _FakeSessionState())

    first = p1_app._auto_sync_textbook_if_changed(store)
    assert first is not None and first >= 1

    second = p1_app._auto_sync_textbook_if_changed(store)
    assert second is None  # unchanged corpus -> no-op, no manual click needed

    (textbook / "algebra.txt").write_text("Completely different content now.", encoding="utf8")
    third = p1_app._auto_sync_textbook_if_changed(store)
    assert third is not None  # edited file -> re-synced automatically


def test_auto_sync_is_a_noop_with_no_textbook_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from lanes.p1_storage import P1Store

    store = P1Store(f"sqlite:///{tmp_path / 'p1.db'}")
    monkeypatch.setattr(p1_app.st, "session_state", _FakeSessionState())

    assert p1_app._auto_sync_textbook_if_changed(store) is None
