"""Unified entry point -- one Streamlit app, one deployed service, switching
between P1/P2/P3's screens via a sidebar radio.

Without this, only one lane's screen could ever be deployed at a time (the
Dockerfile could only launch one script), which meant the pipeline's upload
and grading steps were unreachable once deployed -- breaking the §11
Definition of Done, which requires walking the *whole* path (upload through
feedback), not just reviewing a grade someone produced locally beforehand.

Each lane's app keeps its own file and its own `render()` -- this only
dispatches between them. `st.session_state` is shared across all three
automatically (it's one process, one session), which is what lets a rubric
approved on the P1 screen show up on the P2/P3 screens without a DB round
trip (see p1_app.py's `last_grade`/`last_grade_rubric` and p2_app.py's /
p3_app.py's use of them).

Stage 1 of a multi-stage build added a login gate in front of all of this
(lanes/auth_storage.py): three roles (admin/instructor/student), instructor
registration requires admin approval before the portals below are reachable.

Stage 3 gates WHICH screens a role can see: an admin sees only the
approvals screen, an instructor sees only the P1/P2/P3 grading screens, and
a student sees only their own portal (student_app.py, itself scoped in
Stage 3 to that student's own grades -- lanes/course_storage.py). Before
this, every active/approved role saw every screen, which meant a student
could open the instructor screens too.

Run with: ``streamlit run app.py``
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

import streamlit as st

import p1_app
import p2_app
import p3_app
import student_app
from lanes.auth_storage import User, UserStore

INSTRUCTOR_PAGES = {
    "Upload & Rubric": p1_app.render,
    "Grade & Trace": p2_app.render,
    "Review & Feedback": p3_app.render,
}
STUDENT_PAGES = {
    "Student Feedback Chat": student_app.render,
}

# --------------------------------------------------------------------------- #
# Cookie-based session persistence
# --------------------------------------------------------------------------- #
#
# st.session_state resets on every browser refresh (it's tied to the
# WebSocket connection, not the browser tab), which bounced a logged-in
# user back to the login screen on every reload. The cookie carries a
# signed, expiring token (email + expiry + HMAC) -- never the password or a
# raw user object -- and on restore the email is re-looked-up through
# UserStore fresh from the DB, so a forged, expired, or stale cookie can't
# grant access to an account that doesn't check out right now. Restoring
# only ever repopulates st.session_state["user"]; every existing
# status/role gate below still runs exactly as it does after a fresh login.
#
# Built on Streamlit's own native st.context.cookies (read) and a two-line
# injected script (write) instead of a third-party cookie-manager
# component: those wrap a JS<->Python round trip that needs a `.ready()`/
# `st.stop()` gate on first render and are prone to breaking across
# Streamlit versions -- a native, dependency-free approach can't hang on
# load the way a mismatched component version can.
_SESSION_COOKIE_NAME = "grading_agent_session"
_SESSION_TTL_SECONDS = 12 * 60 * 60  # 12 hours
# Falls back to a per-process random secret when unset -- cookies won't
# survive an actual server restart in that case, but still survive a page
# refresh within one running process, which is the actual goal here. Set
# SESSION_SECRET in a real deployment for persistence across restarts too.
_SESSION_SECRET = os.getenv("SESSION_SECRET") or secrets.token_hex(32)


def _make_session_token(email: str) -> str:
    expiry = int(time.time()) + _SESSION_TTL_SECONDS
    payload = f"{email}:{expiry}"
    signature = hmac.new(_SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def _email_from_session_token(token: str) -> Optional[str]:
    try:
        email, expiry_str, signature = token.split(":", 2)
        expiry = int(expiry_str)
    except (ValueError, AttributeError):
        return None
    if expiry < time.time():
        return None
    expected = hmac.new(_SESSION_SECRET.encode(), f"{email}:{expiry_str}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    return email


def _restore_user_from_cookie(store: UserStore) -> Optional[User]:
    try:
        token = st.context.cookies.get(_SESSION_COOKIE_NAME)
    except Exception:
        return None
    if not token:
        return None
    email = _email_from_session_token(token)
    if email is None:
        return None
    try:
        return store.get_user_by_email(email)
    except Exception:
        return None


def _set_session_cookie(email: str) -> None:
    token = _make_session_token(email)
    st.components.v1.html(
        f"<script>document.cookie=\"{_SESSION_COOKIE_NAME}={token}; path=/; "
        f"max-age={_SESSION_TTL_SECONDS}; SameSite=Lax\";</script>",
        height=0, width=0,
    )


def _clear_session_cookie() -> None:
    st.components.v1.html(
        f"<script>document.cookie=\"{_SESSION_COOKIE_NAME}=; path=/; max-age=0\";</script>",
        height=0, width=0,
    )


def _get_auth_store() -> UserStore:
    if "auth_store" not in st.session_state:
        store = UserStore(os.getenv("DATABASE_URL", "sqlite:///grading_demo.db"))
        # Documented local-dev defaults -- never crash startup over a
        # missing/misconfigured env var; a real deployment sets these for
        # real, but a clean local checkout still needs an admin to exist.
        store.seed_admin(
            os.getenv("ADMIN_EMAIL", "admin@local"),
            os.getenv("ADMIN_PASSWORD", "changeme123"),
        )
        st.session_state.auth_store = store
    return st.session_state.auth_store


def _render_login(store: UserStore) -> None:
    st.title("AI Grading Agent")
    mode = st.radio("", ["Log in", "Register"], horizontal=True, label_visibility="collapsed")

    if mode == "Log in":
        st.subheader("Log in")
        email = st.text_input("Email", key="login-email")
        password = st.text_input("Password", type="password", key="login-password")
        if st.button("Log in", key="login-submit"):
            if not email.strip() or not password:
                st.error("Enter both email and password.")
            else:
                user = store.verify_login(email, password)
                if user is None:
                    st.error("Incorrect email or password.")
                else:
                    st.session_state.user = user
                    _set_session_cookie(user.email)
                    st.rerun()
    else:
        st.subheader("Register")
        st.caption("Admin accounts are not self-registerable -- ask an existing admin.")
        reg_role = st.selectbox("I am a...", ["student", "instructor"], key="reg-role")
        reg_name = st.text_input("Display name", key="reg-name")
        reg_email = st.text_input("Email", key="reg-email")
        reg_password = st.text_input("Password", type="password", key="reg-password")
        if st.button("Register", key="register-submit"):
            if not reg_email.strip() or not reg_password:
                st.error("Email and password are required.")
            else:
                status = "pending" if reg_role == "instructor" else "active"
                try:
                    store.create_user(reg_email, reg_password, reg_role, reg_name, status)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    if status == "pending":
                        st.success("Registered! Your instructor account is awaiting admin approval.")
                    else:
                        st.success("Registered! You can now log in.")


def _render_admin_view(store: UserStore) -> None:
    st.title("AI Grading Agent")
    st.header("Admin: instructor approvals")

    pending = store.list_users(role="instructor", status="pending")
    if not pending:
        st.caption("No instructors awaiting approval.")
    for pending_user in pending:
        col1, col2, col3 = st.columns([3, 1, 1])
        with col1:
            st.write(f"{pending_user.display_name} ({pending_user.email})")
        with col2:
            if st.button("Approve", key=f"approve-{pending_user.id}"):
                store.set_user_status(pending_user.id, "active")
                st.rerun()
        with col3:
            if st.button("Reject", key=f"reject-{pending_user.id}"):
                store.set_user_status(pending_user.id, "rejected")
                st.rerun()

    st.divider()
    st.subheader("All users")
    for existing_user in store.list_users():
        st.write(f"- {existing_user.display_name} ({existing_user.email}) — {existing_user.role}, {existing_user.status}")


def main() -> None:
    st.set_page_config(page_title="AI Grading Agent", layout="wide")
    store = _get_auth_store()

    user: User | None = st.session_state.get("user")
    if user is None:
        user = _restore_user_from_cookie(store)
        if user is not None:
            st.session_state.user = user
    if user is None:
        _render_login(store)
        return

    with st.sidebar:
        st.title("AI Grading Agent")
        st.caption(f"Signed in as {user.display_name} ({user.role})")
        if st.button("Log out"):
            del st.session_state["user"]
            _clear_session_cookie()
            st.rerun()
        st.divider()

    if user.role == "instructor" and user.status == "pending":
        st.info("Your instructor account is awaiting admin approval.")
        return
    if user.role == "instructor" and user.status == "rejected":
        st.error("Your instructor registration was rejected. Contact an admin.")
        return

    if user.role == "admin":
        pages = {"Admin: Approvals": lambda: _render_admin_view(store)}
    elif user.role == "instructor":
        pages = dict(INSTRUCTOR_PAGES)
    else:
        pages = dict(STUDENT_PAGES)

    with st.sidebar:
        page = st.radio("Screen", list(pages.keys()))
        st.divider()
        if not (os.getenv("MODEL_PROVIDER") and os.getenv("MODEL_API_KEY")):
            st.warning("No BYOK model configured — running deterministic offline fallbacks.")
    pages[page]()


if __name__ == "__main__":
    main()
