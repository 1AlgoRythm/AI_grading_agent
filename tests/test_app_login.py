"""AppTest walkthroughs for the login gate + admin-approval flow in app.py
(lanes/auth_storage.py, Stage 1 of a multi-stage auth build). Complements
tests/test_auth.py, which tests UserStore directly -- these drive the
actual login/registration/approval screens the same way a human would.
"""
from __future__ import annotations

from pathlib import Path

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")

_ADMIN_EMAIL = "admin@local"
_ADMIN_PASSWORD = "changeme123"


def _login(at, email: str, password: str) -> None:
    at.text_input(key="login-email").set_value(email).run()
    at.text_input(key="login-password").set_value(password).run()
    at.button(key="login-submit").click().run()


def _register(at, *, role: str, name: str, email: str, password: str) -> None:
    at.radio[0].set_value("Register").run()
    at.selectbox(key="reg-role").select(role).run()
    at.text_input(key="reg-name").set_value(name).run()
    at.text_input(key="reg-email").set_value(email).run()
    at.text_input(key="reg-password").set_value(password).run()
    at.button(key="register-submit").click().run()


def test_unauthenticated_user_sees_a_login_screen_not_the_app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()

    assert len(at.sidebar.radio) == 0  # no screen picker until logged in
    assert any(t.label == "Email" for t in at.text_input)
    assert any(t.label == "Password" for t in at.text_input)


def test_wrong_password_shows_a_clear_error_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()
    _login(at, _ADMIN_EMAIL, "definitely-the-wrong-password")

    assert not at.exception
    assert any("incorrect email or password" in e.value.lower() for e in at.error)
    assert len(at.sidebar.radio) == 0  # still not let in


def test_seeded_admin_can_log_in_and_sees_only_the_admin_screen(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()
    _login(at, _ADMIN_EMAIL, _ADMIN_PASSWORD)

    assert not at.exception
    # Stage 3: admin is gated to the approvals screen only -- no grading
    # screens, exactly the "no grading screens needed" role-gating spec.
    assert list(at.sidebar.radio[0].options) == ["Admin: Approvals"]


def test_student_self_registers_active_and_can_log_in_immediately(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()
    _register(at, role="student", name="Stu Dent", email="stu@example.com", password="pw12345")
    assert any("registered" in s.value.lower() for s in at.success)

    at.radio[0].set_value("Log in").run()
    _login(at, "stu@example.com", "pw12345")

    assert not at.exception
    # Stage 3: a student is gated to their own portal only -- no instructor
    # or admin screens on the sidebar at all.
    assert list(at.sidebar.radio[0].options) == ["Student Feedback Chat"]


def test_instructor_registration_is_pending_and_blocked_until_admin_approves(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()
    _register(at, role="instructor", name="Prof", email="prof@example.com", password="pw12345")

    at.radio[0].set_value("Log in").run()
    _login(at, "prof@example.com", "pw12345")

    # Logged in (credentials are correct) but blocked from the portals --
    # the store doesn't gate this, the app layer does.
    assert not at.exception
    assert len(at.sidebar.radio) == 0
    assert any("awaiting admin approval" in i.value.lower() for i in at.info)


def test_admin_approves_a_pending_instructor_who_can_then_log_in(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()
    _register(at, role="instructor", name="Prof", email="prof@example.com", password="pw12345")

    at.radio[0].set_value("Log in").run()
    _login(at, _ADMIN_EMAIL, _ADMIN_PASSWORD)
    at.sidebar.radio[0].set_value("Admin: Approvals").run()
    all_text = [w.value for w in at.markdown] + [w.value for w in at.text]
    assert any("Prof" in w and "prof@example.com" in w for w in all_text)

    next(b for b in at.button if b.label == "Approve").click().run()

    # Log out, log back in as the now-approved instructor.
    next(b for b in at.button if b.label == "Log out").click().run()
    _login(at, "prof@example.com", "pw12345")

    assert not at.exception
    assert at.sidebar.radio[0].value == "Upload & Rubric"
    assert not any("awaiting admin approval" in i.value.lower() for i in at.info)


def test_admin_can_reject_a_pending_instructor(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()
    _register(at, role="instructor", name="Prof", email="prof@example.com", password="pw12345")

    at.radio[0].set_value("Log in").run()
    _login(at, _ADMIN_EMAIL, _ADMIN_PASSWORD)
    at.sidebar.radio[0].set_value("Admin: Approvals").run()
    next(b for b in at.button if b.label == "Reject").click().run()
    next(b for b in at.button if b.label == "Log out").click().run()

    _login(at, "prof@example.com", "pw12345")

    assert not at.exception
    assert len(at.sidebar.radio) == 0
    assert any("rejected" in e.value.lower() for e in at.error)


def test_logout_returns_to_the_login_screen(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()
    _login(at, _ADMIN_EMAIL, _ADMIN_PASSWORD)
    assert len(at.sidebar.radio) == 1

    next(b for b in at.button if b.label == "Log out").click().run()

    assert not at.exception
    assert len(at.sidebar.radio) == 0
    assert any(t.label == "Email" for t in at.text_input)


def test_duplicate_email_registration_shows_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()
    _register(at, role="student", name="A", email="dup@example.com", password="pw12345")
    _register(at, role="student", name="B", email="dup@example.com", password="pw67890")

    assert not at.exception
    assert any("already exists" in e.value.lower() for e in at.error)
