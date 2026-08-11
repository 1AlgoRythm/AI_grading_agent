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

import os

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
        _render_login(store)
        return

    with st.sidebar:
        st.title("AI Grading Agent")
        st.caption(f"Signed in as {user.display_name} ({user.role})")
        if st.button("Log out"):
            del st.session_state["user"]
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
