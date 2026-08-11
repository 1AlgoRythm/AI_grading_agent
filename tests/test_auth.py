"""Tests for lanes/auth_storage.py -- users, password hashing, login, and
the admin-approval flow for instructors. Stage 1 of a multi-stage build:
auth only, isolated from grading entirely.
"""
from __future__ import annotations

import pytest

from lanes.auth_storage import UserStore, hash_password, verify_password


def test_hash_password_verifies_the_correct_password_and_rejects_a_wrong_one():
    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored) is True
    assert verify_password("wrong password", stored) is False


def test_hash_password_uses_a_random_salt_so_the_same_password_hashes_differently():
    assert hash_password("same password") != hash_password("same password")


def test_verify_password_never_raises_on_a_malformed_stored_hash():
    assert verify_password("anything", "not-a-real-hash-at-all") is False
    assert verify_password("anything", "") is False


def test_create_user_and_get_user_by_email_round_trip(tmp_path):
    store = UserStore(f"sqlite:///{tmp_path / 'auth.db'}")

    created = store.create_user("Student@Example.com", "hunter2", "student", "Stu Dent", "active")

    assert created.role == "student"
    assert created.status == "active"
    assert created.email == "student@example.com"  # normalized to lowercase

    found = store.get_user_by_email("STUDENT@example.com")  # lookup is also case-insensitive
    assert found is not None
    assert found.id == created.id
    assert found.display_name == "Stu Dent"


def test_get_user_by_email_returns_none_for_an_unknown_email(tmp_path):
    store = UserStore(f"sqlite:///{tmp_path / 'auth.db'}")
    assert store.get_user_by_email("nobody@example.com") is None
    assert store.user_exists("nobody@example.com") is False


def test_create_user_enforces_unique_email(tmp_path):
    store = UserStore(f"sqlite:///{tmp_path / 'auth.db'}")
    store.create_user("dup@example.com", "pw1", "student", "First", "active")

    with pytest.raises(ValueError, match="already exists"):
        store.create_user("dup@example.com", "pw2", "instructor", "Second", "pending")


def test_create_user_rejects_an_unknown_role_or_status(tmp_path):
    store = UserStore(f"sqlite:///{tmp_path / 'auth.db'}")
    with pytest.raises(ValueError, match="unknown role"):
        store.create_user("a@example.com", "pw", "superuser", "A", "active")
    with pytest.raises(ValueError, match="unknown status"):
        store.create_user("b@example.com", "pw", "student", "B", "banned")


def test_create_user_requires_email_and_password(tmp_path):
    store = UserStore(f"sqlite:///{tmp_path / 'auth.db'}")
    with pytest.raises(ValueError):
        store.create_user("", "pw", "student", "A", "active")
    with pytest.raises(ValueError):
        store.create_user("a@example.com", "", "student", "A", "active")


def test_instructor_registers_pending_then_admin_approval_flips_to_active(tmp_path):
    store = UserStore(f"sqlite:///{tmp_path / 'auth.db'}")
    instructor = store.create_user("prof@example.com", "pw", "instructor", "Prof", "pending")

    # Pending: verify_login still succeeds (credentials are correct) -- the
    # app layer is what gates a pending instructor out of the portals, not
    # the store. The store's job is only "are these credentials correct."
    logged_in = store.verify_login("prof@example.com", "pw")
    assert logged_in is not None
    assert logged_in.status == "pending"

    store.set_user_status(instructor.id, "active")

    reloaded = store.get_user_by_email("prof@example.com")
    assert reloaded.status == "active"


def test_set_user_status_rejects_an_unknown_status_or_id(tmp_path):
    store = UserStore(f"sqlite:///{tmp_path / 'auth.db'}")
    user = store.create_user("a@example.com", "pw", "student", "A", "active")
    with pytest.raises(ValueError, match="unknown status"):
        store.set_user_status(user.id, "banned")
    with pytest.raises(KeyError):
        store.set_user_status("not-a-real-id", "active")


def test_verify_login_returns_none_for_wrong_password_or_nonexistent_user(tmp_path):
    store = UserStore(f"sqlite:///{tmp_path / 'auth.db'}")
    store.create_user("a@example.com", "correct", "student", "A", "active")

    assert store.verify_login("a@example.com", "wrong") is None
    assert store.verify_login("nobody@example.com", "anything") is None
    assert store.verify_login("a@example.com", "correct") is not None


def test_list_users_filters_by_role_and_status(tmp_path):
    store = UserStore(f"sqlite:///{tmp_path / 'auth.db'}")
    store.create_user("admin@example.com", "pw", "admin", "Admin", "active")
    store.create_user("pending-prof@example.com", "pw", "instructor", "Prof P", "pending")
    store.create_user("active-prof@example.com", "pw", "instructor", "Prof A", "active")
    store.create_user("student@example.com", "pw", "student", "Stu", "active")

    instructors = store.list_users(role="instructor")
    assert {u.email for u in instructors} == {"pending-prof@example.com", "active-prof@example.com"}

    pending_instructors = store.list_users(role="instructor", status="pending")
    assert [u.email for u in pending_instructors] == ["pending-prof@example.com"]

    assert len(store.list_users()) == 4


def test_seed_admin_is_idempotent_and_never_raises(tmp_path):
    store = UserStore(f"sqlite:///{tmp_path / 'auth.db'}")

    store.seed_admin("admin@local", "changeme123")
    store.seed_admin("admin@local", "changeme123")  # second call must not crash or duplicate

    admins = store.list_users(role="admin")
    assert len(admins) == 1
    assert admins[0].email == "admin@local"


def test_seed_admin_does_nothing_once_any_admin_already_exists(tmp_path):
    store = UserStore(f"sqlite:///{tmp_path / 'auth.db'}")
    store.create_user("first-admin@example.com", "pw", "admin", "First", "active")

    store.seed_admin("second-admin@example.com", "pw2")  # must not create a second admin

    admins = store.list_users(role="admin")
    assert len(admins) == 1
    assert admins[0].email == "first-admin@example.com"
