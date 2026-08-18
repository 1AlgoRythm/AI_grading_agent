"""Shared Streamlit upload-to-temp-file helper for p1_app.py and student_app.py."""
from __future__ import annotations

import tempfile
from pathlib import Path


def write_uploaded_file(uploaded) -> str:
    """Write an uploaded file to a temp path, preserving its original name
    (mkdtemp + real filename) instead of NamedTemporaryFile's random name --
    otherwise ingest_assignment/ingest_submission derive the label/student
    handle from a meaningless "tmpXXXXXXXX" stem."""
    tmp_dir = Path(tempfile.mkdtemp())
    dest = tmp_dir / (uploaded.name or "upload.txt")
    dest.write_bytes(uploaded.getvalue())
    return str(dest)
