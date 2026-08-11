"""User accounts and authentication -- database-backed, three roles
(admin/instructor/student), with an admin-approval gate for instructors.

Stage 1 of a multi-stage build: auth only, self-contained. Mirrors the
existing store pattern (lanes/p1_storage.py, lanes/p2_storage.py,
lanes/p3_storage.py) exactly -- its own isolated DeclarativeBase,
create_all in __init__, the same DATABASE_URL every other store already
uses. An additive table only; creating it never touches, requires
migrating, or can conflict with any existing table, since each store's
Base.metadata is independent of the others'.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from contracts import new_id, now

__all__ = ["ROLES", "STATUSES", "User", "UserStore", "hash_password", "verify_password"]

ROLES = ("admin", "instructor", "student")
STATUSES = ("active", "pending", "rejected")

# 260,000 rounds of PBKDF2-HMAC-SHA256 was OWASP's recommended floor as of
# 2023 -- stdlib-only (hashlib.pbkdf2_hmac) rather than adding a new
# dependency (werkzeug/passlib) for something the standard library already
# covers well.
_PBKDF2_ROUNDS = 260_000


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    """Returns "<salt_hex>$<digest_hex>". Never store the plaintext
    password or a bare unsalted hash of it."""
    salt = salt if salt is not None else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf8"), salt, _PBKDF2_ROUNDS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Never raises on a malformed stored hash -- treated as "doesn't
    match" rather than a 500 on login."""
    try:
        salt_hex, _ = stored_hash.split("$", 1)
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    return hash_password(password, salt) == stored_hash


@dataclass(frozen=True)
class User:
    """Plain data, never the raw ORM row -- same reason P1Store.load_assignment
    returns a contracts.Assignment, not an AssignmentRecord: a SQLAlchemy
    object touched after its Session has closed can raise
    DetachedInstanceError on attribute access. Deliberately excludes
    password_hash; nothing outside this module ever needs to see it."""
    id: str
    email: str
    role: str
    display_name: str
    status: str
    created_at: datetime


class Base(DeclarativeBase):
    pass


class UserRecord(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    display_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _to_user(record: UserRecord) -> User:
    return User(
        id=record.id, email=record.email, role=record.role,
        display_name=record.display_name, status=record.status, created_at=record.created_at,
    )


class UserStore:
    """Repository for user accounts and login -- same shape as
    P1Store/P2Store/P3Store."""

    def __init__(self, database_url: str) -> None:
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(database_url, connect_args=connect_args)
        Base.metadata.create_all(self.engine)

    def user_exists(self, email: str) -> bool:
        return self.get_user_by_email(email) is not None

    def create_user(
        self, email: str, password: str, role: str, display_name: str, status: str,
    ) -> User:
        email = email.strip().lower()
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}; expected one of {ROLES}")
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}; expected one of {STATUSES}")
        if not email or not password:
            raise ValueError("email and password are required")
        if self.user_exists(email):
            raise ValueError(f"a user with email {email!r} already exists")

        record = UserRecord(
            id=str(new_id()), email=email, password_hash=hash_password(password),
            role=role, display_name=(display_name or email).strip(), status=status,
            created_at=now(),
        )
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            return _to_user(record)

    def get_user_by_email(self, email: str) -> Optional[User]:
        email = email.strip().lower()
        with Session(self.engine) as session:
            record = session.scalars(select(UserRecord).where(UserRecord.email == email)).first()
            return _to_user(record) if record is not None else None

    def verify_login(self, email: str, password: str) -> Optional[User]:
        with Session(self.engine) as session:
            record = session.scalars(
                select(UserRecord).where(UserRecord.email == email.strip().lower())
            ).first()
            if record is None or not verify_password(password, record.password_hash):
                return None
            return _to_user(record)

    def list_users(self, role: Optional[str] = None, status: Optional[str] = None) -> list[User]:
        query = select(UserRecord)
        if role is not None:
            query = query.where(UserRecord.role == role)
        if status is not None:
            query = query.where(UserRecord.status == status)
        query = query.order_by(UserRecord.created_at)
        with Session(self.engine) as session:
            return [_to_user(record) for record in session.scalars(query).all()]

    def set_user_status(self, user_id: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}; expected one of {STATUSES}")
        with Session(self.engine) as session:
            record = session.get(UserRecord, user_id)
            if record is None:
                raise KeyError(f"no user with id {user_id!r}")
            record.status = status
            session.commit()

    def seed_admin(self, email: str, password: str, display_name: str = "Admin") -> None:
        """Idempotent and safe to call on every app startup: creates the
        default admin only if no admin exists yet. Never raises -- a
        misconfigured env var must not crash the whole app on boot."""
        if self.list_users(role="admin"):
            return
        try:
            self.create_user(email, password, role="admin", display_name=display_name, status="active")
        except ValueError:
            # A non-admin user already holds this exact email -- extremely
            # unlikely with the documented default, but seeding the admin
            # is still not worth crashing startup over.
            pass
