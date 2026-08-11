"""Shared in-session cache keyed by Grade.id.

p2_app.py and p3_app.py each used to hold their own independent copy of
"the current grade" -- fine for the single just-graded-this-session
submission (both copies trace back to the same object via p1_app.py's
`last_grade`), but the instant either page instead loads an *arbitrary*
submission from the database (p3_app.py's "Load this submission" picker is
the only way to do that today), it gets a fresh, independent object that
silently diverges from whatever the other page is holding the moment either
one changes something -- an override on one tab wouldn't show up on the
other until a manual reload, even though the database itself was already
correct.

Routing every page's grade through this cache means the *first* page to see
a given grade.id in this session wins: every later page gets that exact
same object back instead of its own copy, so a mutation either page makes
is visible to both immediately, with zero extra DB round-trips.
"""
from __future__ import annotations

import streamlit as st

from contracts import Grade


def shared_grade(grade: Grade) -> Grade:
    cache = st.session_state.setdefault("grade_cache", {})
    return cache.setdefault(grade.id, grade)
