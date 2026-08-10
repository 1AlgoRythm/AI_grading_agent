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
