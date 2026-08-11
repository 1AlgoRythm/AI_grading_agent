"""Tests for app.py's cookie-based session persistence.

st.session_state resets on every browser refresh -- these cover the signed
token (so a forged or tampered cookie can never grant access) and the
restore path (so a valid cookie on a fresh session correctly logs the user
back in without ever bouncing through the login screen), the same way a
real refresh would.
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import app

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")

_ADMIN_EMAIL = "admin@local"
_ADMIN_PASSWORD = "changeme123"


def test_a_freshly_minted_token_round_trips_to_the_same_email():
    token = app._make_session_token("someone@example.com")
    assert app._email_from_session_token(token) == "someone@example.com"


def test_a_tampered_signature_is_rejected():
    token = app._make_session_token("someone@example.com")
    email, expiry, signature = token.split(":", 2)
    tampered = f"{email}:{expiry}:{signature[:-1]}{'0' if signature[-1] != '0' else '1'}"
    assert app._email_from_session_token(tampered) is None


def test_a_forged_email_without_the_real_signature_is_rejected():
    # Simulates someone hand-editing the cookie value in devtools to claim
    # a different account -- the signature won't match the new email.
    token = app._make_session_token("student@example.com")
    _, expiry, signature = token.split(":", 2)
    forged = f"admin@local:{expiry}:{signature}"
    assert app._email_from_session_token(forged) is None


def test_an_expired_token_is_rejected():
    email, expiry, signature = app._make_session_token("someone@example.com").split(":", 2)
    expired = f"{email}:{int(time.time()) - 10}:{signature}"
    assert app._email_from_session_token(expired) is None


def test_garbage_input_never_raises():
    for garbage in ("", "not-a-token", "a:b", "a:b:c:d", None):
        assert app._email_from_session_token(garbage) is None


def test_restore_looks_up_the_user_fresh_from_the_store_not_the_cookie(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    store = app.UserStore(f"sqlite:///{tmp_path / 'app.db'}")
    store.seed_admin(_ADMIN_EMAIL, _ADMIN_PASSWORD)

    token = app._make_session_token(_ADMIN_EMAIL)
    monkeypatch.setattr(app.st, "context", SimpleNamespace(cookies={app._SESSION_COOKIE_NAME: token}))

    user = app._restore_user_from_cookie(store)

    assert user is not None
    assert user.email == _ADMIN_EMAIL
    assert user.role == "admin"


def test_restore_returns_none_when_the_account_no_longer_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    store = app.UserStore(f"sqlite:///{tmp_path / 'app.db'}")

    token = app._make_session_token("nobody@example.com")
    monkeypatch.setattr(app.st, "context", SimpleNamespace(cookies={app._SESSION_COOKIE_NAME: token}))

    assert app._restore_user_from_cookie(store) is None


def test_restore_returns_none_with_no_cookie_present(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    store = app.UserStore(f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setattr(app.st, "context", SimpleNamespace(cookies={}))

    assert app._restore_user_from_cookie(store) is None


def test_a_fresh_session_with_no_cookie_still_shows_the_login_screen(tmp_path, monkeypatch):
    # AppTest.from_file() execs app.py as its own isolated script -- it does
    # NOT reuse this test's already-imported `app` module object, so nothing
    # patched onto `app.*` here (st.context, _set_session_cookie, etc.) is
    # visible to the script AppTest actually runs. That means a real browser
    # cookie can't be injected into an AppTest run at all; what CAN be
    # verified here is the baseline AppTest already exercises for every
    # other login test -- no session, no cookie, login screen -- to confirm
    # wiring in the restore-from-cookie call didn't disturb it.
    db_url = f"sqlite:///{tmp_path / 'app.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    app.UserStore(db_url).seed_admin(_ADMIN_EMAIL, _ADMIN_PASSWORD)

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()

    assert not at.exception
    assert any(t.label == "Password" for t in at.text_input)


def test_logging_in_and_logging_out_still_work_with_the_cookie_calls_wired_in(tmp_path, monkeypatch):
    # Real cookie injection/interception isn't reachable through AppTest
    # (see the note above), so this is the achievable version: the real
    # _set_session_cookie/_clear_session_cookie calls (each rendering a
    # `st.components.v1.html` element) now run on every login/logout inside
    # main()'s actual code path, and must not raise or otherwise break the
    # existing login/logout flow that test_app_login.py already covers.
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()
    at.text_input(key="login-email").set_value(_ADMIN_EMAIL).run()
    at.text_input(key="login-password").set_value(_ADMIN_PASSWORD).run()
    at.button(key="login-submit").click().run()

    assert not at.exception
    assert list(at.sidebar.radio[0].options) == ["Admin: Approvals"]

    next(b for b in at.button if b.label == "Log out").click().run()

    assert not at.exception
    assert any(t.label == "Password" for t in at.text_input)  # back to the login screen


def test_pending_cookie_write_is_consumed_within_one_interaction_not_left_dangling(tmp_path, monkeypatch):
    # The actual _set_session_cookie/_clear_session_cookie calls are
    # deferred a run past the login/logout click (see the comments in
    # app.py's main() and _render_login) so the injected iframe isn't torn
    # down by the immediate st.rerun() before the browser can mount it.
    # AppTest.run() follows through that one rerun automatically, so by the
    # time it returns, the pending flag must already be consumed -- if it
    # were left set, the cookie write would never actually happen (it's
    # only ever drained at the top of main()).
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()
    at.text_input(key="login-email").set_value(_ADMIN_EMAIL).run()
    at.text_input(key="login-password").set_value(_ADMIN_PASSWORD).run()
    at.button(key="login-submit").click().run()

    assert not at.exception
    assert "_pending_set_cookie_email" not in at.session_state

    next(b for b in at.button if b.label == "Log out").click().run()

    assert not at.exception
    assert "_pending_clear_cookie" not in at.session_state
