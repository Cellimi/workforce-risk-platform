"""The database behind sign-in and engagements.

SQLite by default, so a small firm can install the product without running a database
server; any SQLAlchemy URL works through ``WRI_DATABASE_URL``.

What lives here is deliberately small: who may sign in, which engagements exist, who belongs
to each one and in what role, and which assumptions an engagement has overridden. Client HR
data is NOT stored in these tables -- it stays in the engagement's own folder on disk.

Every method returns plain frozen records, never live ORM objects, so nothing outside this
module can hold a database session open or change a row by accident.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    create_engine,
    delete,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from wri_engine.access.roles import Role
from wri_engine.store.passwords import DUMMY_HASH, hash_password, verify_password

MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15
SECTORS = ("public_sector",)


class AuthError(Exception):
    """Sign-in failed. The message is safe to show the caller."""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class _User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(300))
    is_firm_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class _Session(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class _Engagement(Base):
    __tablename__ = "engagements"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    client_name: Mapped[str] = mapped_column(String(200))
    sector: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class _Membership(Base):
    __tablename__ = "memberships"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    engagement_id: Mapped[str] = mapped_column(ForeignKey("engagements.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))


class _Override(Base):
    __tablename__ = "assumption_overrides"
    engagement_id: Mapped[str] = mapped_column(ForeignKey("engagements.id"), primary_key=True)
    assumption_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(String(60))
    reason: Mapped[str] = mapped_column(String(500), default="")
    set_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    set_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


@dataclass(frozen=True)
class UserRecord:
    id: int
    email: str
    name: str
    is_firm_admin: bool
    is_active: bool


@dataclass(frozen=True)
class EngagementRecord:
    id: str
    name: str
    client_name: str
    sector: str
    status: str
    created_at: datetime


@dataclass(frozen=True)
class OverrideRecord:
    assumption_id: str
    value: Decimal
    reason: str
    set_by: str
    set_at: datetime


def _user(row: _User) -> UserRecord:
    return UserRecord(row.id, row.email, row.name, row.is_firm_admin, row.is_active)


def _engagement(row: _Engagement) -> EngagementRecord:
    return EngagementRecord(
        row.id, row.name, row.client_name, row.sector, row.status, row.created_at
    )


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if "@" not in email or len(email) > 320 or " " in email:
        raise ValueError(f"{email!r} is not an email address")
    return email


class Store:
    def __init__(self, database_url: str):
        kwargs: dict = {}
        if database_url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if ":memory:" in database_url:
                kwargs["poolclass"] = StaticPool
        self.engine = create_engine(database_url, **kwargs)
        Base.metadata.create_all(self.engine)
        self._session = sessionmaker(self.engine, expire_on_commit=False)

    def close(self) -> None:
        """Release every database connection. Called when the API shuts down."""
        self.engine.dispose()

    # -- users ---------------------------------------------------------------
    def create_user(
        self, email: str, password: str, *, name: str = "", is_firm_admin: bool = False
    ) -> UserRecord:
        email = normalize_email(email)
        with self._session.begin() as db:
            if db.scalar(select(_User).where(_User.email == email)):
                raise ValueError(f"a user with email {email} already exists")
            row = _User(
                email=email,
                name=name.strip(),
                password_hash=hash_password(password),
                is_firm_admin=is_firm_admin,
            )
            db.add(row)
            db.flush()
            return _user(row)

    def get_user_by_email(self, email: str) -> UserRecord | None:
        with self._session() as db:
            row = db.scalar(select(_User).where(_User.email == email.strip().lower()))
            return _user(row) if row else None

    def list_users(self) -> list[UserRecord]:
        with self._session() as db:
            return [_user(r) for r in db.scalars(select(_User).order_by(_User.email))]

    def count_users(self) -> int:
        return len(self.list_users())

    def set_user_active(self, user_id: int, active: bool) -> None:
        with self._session.begin() as db:
            row = db.get(_User, user_id)
            if row is None:
                raise KeyError(user_id)
            row.is_active = active
            if not active:
                db.execute(delete(_Session).where(_Session.user_id == user_id))

    # -- sign-in -------------------------------------------------------------
    def sign_in(
        self, email: str, password: str, *, session_hours: int = 12
    ) -> tuple[str, UserRecord]:
        """Check the password and open a session. Returns the bearer token (shown once; only
        its hash is stored) and the user. Every failure raises the same message."""
        failure = AuthError("email or password is incorrect")
        with self._session.begin() as db:
            row = db.scalar(select(_User).where(_User.email == email.strip().lower()))
            if row is None:
                verify_password(password, DUMMY_HASH)
                raise failure
            now = _now()
            if row.locked_until and row.locked_until > now:
                raise AuthError(
                    f"too many failed sign-ins; this account is locked for up to "
                    f"{LOCKOUT_MINUTES} minutes"
                )
            if not verify_password(password, row.password_hash) or not row.is_active:
                row.failed_logins += 1
                if row.failed_logins >= MAX_FAILED_LOGINS:
                    row.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
                    row.failed_logins = 0
                db.commit()
                raise failure
            row.failed_logins = 0
            row.locked_until = None
            token = secrets.token_urlsafe(32)
            db.add(
                _Session(
                    token_hash=_hash_token(token),
                    user_id=row.id,
                    expires_at=now + timedelta(hours=session_hours),
                )
            )
            return token, _user(row)

    def user_for_token(self, token: str) -> UserRecord | None:
        with self._session.begin() as db:
            session = db.get(_Session, _hash_token(token))
            if session is None:
                return None
            if session.expires_at <= _now():
                db.delete(session)
                return None
            row = db.get(_User, session.user_id)
            return _user(row) if row and row.is_active else None

    def sign_out(self, token: str) -> None:
        with self._session.begin() as db:
            db.execute(delete(_Session).where(_Session.token_hash == _hash_token(token)))

    # -- engagements ---------------------------------------------------------
    def create_engagement(
        self, *, name: str, client_name: str, sector: str, created_by: int
    ) -> EngagementRecord:
        if sector not in SECTORS:
            raise ValueError(
                f"sector {sector!r} is not available. Cost models installed: {', '.join(SECTORS)}"
            )
        if not name.strip() or not client_name.strip():
            raise ValueError("an engagement needs a name and a client name")
        with self._session.begin() as db:
            row = _Engagement(
                id=secrets.token_hex(8),
                name=name.strip(),
                client_name=client_name.strip(),
                sector=sector,
                created_by=created_by,
            )
            db.add(row)
            db.flush()
            return _engagement(row)

    def get_engagement(self, engagement_id: str) -> EngagementRecord | None:
        with self._session() as db:
            row = db.get(_Engagement, engagement_id)
            return _engagement(row) if row else None

    def engagements_for(self, user: UserRecord) -> list[tuple[EngagementRecord, Role]]:
        """Engagements this user may open, with their role in each. A firm administrator
        sees every engagement as its admin."""
        with self._session() as db:
            if user.is_firm_admin:
                rows = db.scalars(select(_Engagement).order_by(_Engagement.created_at))
                return [(_engagement(r), Role.ADMIN) for r in rows]
            rows = db.execute(
                select(_Engagement, _Membership.role)
                .join(_Membership, _Membership.engagement_id == _Engagement.id)
                .where(_Membership.user_id == user.id)
                .order_by(_Engagement.created_at)
            )
            return [(_engagement(e), Role(role)) for e, role in rows]

    def role_in(self, user: UserRecord, engagement_id: str) -> Role | None:
        with self._session() as db:
            if db.get(_Engagement, engagement_id) is None:
                return None
            if user.is_firm_admin:
                return Role.ADMIN
            member = db.get(_Membership, (user.id, engagement_id))
            return Role(member.role) if member else None

    def set_member(self, engagement_id: str, user_id: int, role: Role | None) -> None:
        """Add a user to an engagement, change their role, or (role=None) remove them."""
        with self._session.begin() as db:
            if db.get(_Engagement, engagement_id) is None or db.get(_User, user_id) is None:
                raise KeyError("unknown engagement or user")
            member = db.get(_Membership, (user_id, engagement_id))
            if role is None:
                if member:
                    db.delete(member)
            elif member:
                member.role = str(role)
            else:
                db.add(_Membership(user_id=user_id, engagement_id=engagement_id, role=str(role)))

    def members(self, engagement_id: str) -> list[tuple[UserRecord, Role]]:
        with self._session() as db:
            rows = db.execute(
                select(_User, _Membership.role)
                .join(_Membership, _Membership.user_id == _User.id)
                .where(_Membership.engagement_id == engagement_id)
                .order_by(_User.email)
            )
            return [(_user(u), Role(role)) for u, role in rows]

    # -- assumption overrides ------------------------------------------------
    def overrides(self, engagement_id: str) -> list[OverrideRecord]:
        with self._session() as db:
            rows = db.execute(
                select(_Override, _User.email)
                .join(_User, _User.id == _Override.set_by)
                .where(_Override.engagement_id == engagement_id)
                .order_by(_Override.assumption_id)
            )
            return [
                OverrideRecord(o.assumption_id, Decimal(o.value), o.reason, email, o.set_at)
                for o, email in rows
            ]

    def set_override(
        self, engagement_id: str, assumption_id: str, value: Decimal, *, reason: str, set_by: int
    ) -> None:
        with self._session.begin() as db:
            row = db.get(_Override, (engagement_id, assumption_id))
            if row is None:
                row = _Override(engagement_id=engagement_id, assumption_id=assumption_id)
                db.add(row)
            row.value = str(value)
            row.reason = reason.strip()
            row.set_by = set_by
            row.set_at = _now()

    def clear_override(self, engagement_id: str, assumption_id: str) -> bool:
        with self._session.begin() as db:
            row = db.get(_Override, (engagement_id, assumption_id))
            if row is None:
                return False
            db.delete(row)
            return True
