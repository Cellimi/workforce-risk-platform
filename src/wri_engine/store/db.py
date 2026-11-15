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
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    create_engine,
    delete,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from wri_engine.access.roles import Role
from wri_engine.licensing.usage import GENESIS, entry_hash
from wri_engine.sectors import get_pack, sector_ids
from wri_engine.store.passwords import DUMMY_HASH, hash_password, verify_password

MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15
#: The shape of the data this version of the product reads and writes. Raise it by one
#: whenever a change means an OLDER product could no longer read the database safely, and
#: add the step that brings an existing database up to it in `_upgrade_schema`.
#:   1  everything up to and including engagement license keys and the usage log
SCHEMA_VERSION = 1
#: The sectors an engagement may be opened for. Read from the installed cost models rather
#: than listed here, so the server can never refuse a sector the engine can actually cost --
#: or accept one it cannot.
SECTORS = sector_ids()


class AuthError(Exception):
    """Sign-in failed. The message is safe to show the caller."""


class SchemaTooNew(RuntimeError):
    """The database was last used by a newer product than this one."""

    def __init__(self, found: int):
        self.found = found
        super().__init__(
            f"this installation's data was last used by a newer version of the product "
            f"(data version {found}; this version understands up to {SCHEMA_VERSION}). "
            f"Nothing was changed. Install the newer version again, or restore the backup "
            f"taken before the upgrade."
        )


def stored_schema_version(database_url: str) -> int | None:
    """The data version a database records, without opening it as an installation or
    changing it. None if it has never been opened by a version that records one."""
    if database_url.startswith("sqlite:///") and ":memory:" not in database_url:
        # SQLite makes an empty file the moment it is connected to. Looking must not.
        from pathlib import Path

        if not Path(database_url.removeprefix("sqlite:///")).exists():
            return None
    engine = create_engine(database_url)
    try:
        if "installation" not in inspect(engine).get_table_names():
            return None
        with engine.connect() as connection:
            value = connection.execute(
                text("SELECT value FROM installation WHERE key = 'schema_version'")
            ).scalar()
        return int(value) if value is not None else None
    finally:
        engine.dispose()


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
    #: The kind of work the client's people mostly do, where the sector loads benefits by
    #: occupation. Null means not selected: the all-occupations figure is used.
    occupation_group: Mapped[str | None] = mapped_column(String(40), nullable=True, default=None)
    #: The license key this engagement runs under, and its id for finding a key used twice.
    license_key: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    license_key_id: Mapped[str | None] = mapped_column(String(40), nullable=True, default=None)
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


class _Usage(Base):
    """One entry of the usage log. Each carries a hash of itself and of the entry before
    it; see `wri_engine.licensing.usage`."""

    __tablename__ = "license_usage"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[str] = mapped_column(String(32))
    event: Mapped[str] = mapped_column(String(40))
    engagement_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    key_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="{}")
    previous_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class _Installation(Base):
    """Facts about the installation itself: the data version, and which product last
    opened the database. Read before anything else at start-up."""

    __tablename__ = "installation"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))


class _Firm(Base):
    """The consulting firm that runs this installation. One row at most."""

    __tablename__ = "firm_profile"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    logo: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, default=None)
    logo_mime: Mapped[str | None] = mapped_column(String(40), nullable=True, default=None)


@dataclass(frozen=True)
class FirmRecord:
    """Whose reports these are: the name and logo printed on a client deliverable."""

    name: str = ""
    logo: bytes | None = None
    logo_mime: str | None = None


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
    occupation_group: str | None = None
    license_key: str | None = None
    license_key_id: str | None = None


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
        row.id,
        row.name,
        row.client_name,
        row.sector,
        row.status,
        row.created_at,
        row.occupation_group,
        row.license_key,
        row.license_key_id,
    )


def check_occupation_group(sector: str, occupation_group: str | None) -> str | None:
    """The selection as it will be stored, or a ValueError the caller can show.

    Only a sector whose cost model loads benefits by occupation offers the selection, and
    only the groups that model has a multiplier for can be chosen. Blank means "not
    selected", which is always allowed.
    """
    if not occupation_group:
        return None
    offered = get_pack(sector).benefits_group_by_occupation
    if not offered:
        raise ValueError(
            f"the {sector} cost model does not load benefits by occupation group, so one "
            f"cannot be selected"
        )
    if occupation_group not in offered:
        raise ValueError(
            f"occupation group {occupation_group!r} is not one of: "
            f"{', '.join(str(group) for group in offered)}"
        )
    return str(occupation_group)


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
        # Looked at before anything is created or altered: an older product must leave a
        # newer database exactly as it found it.
        found = self._recorded_schema_version()
        if found is not None and found > SCHEMA_VERSION:
            self.engine.dispose()
            raise SchemaTooNew(found)
        Base.metadata.create_all(self.engine)
        self._upgrade_schema()
        self._record_schema_version()
        self._session = sessionmaker(self.engine, expire_on_commit=False)

    def _recorded_schema_version(self) -> int | None:
        if "installation" not in inspect(self.engine).get_table_names():
            return None
        with self.engine.connect() as connection:
            value = connection.execute(
                text("SELECT value FROM installation WHERE key = 'schema_version'")
            ).scalar()
        return int(value) if value is not None else None

    def _record_schema_version(self) -> None:
        """Note the data version and which product last opened the database."""
        from wri_engine import __version__

        with self.engine.begin() as connection:
            for key, value in (
                ("schema_version", str(SCHEMA_VERSION)),
                ("product_version", __version__),
            ):
                updated = connection.execute(
                    text("UPDATE installation SET value = :value WHERE key = :key"),
                    {"key": key, "value": value},
                )
                if updated.rowcount == 0:
                    connection.execute(
                        text("INSERT INTO installation (key, value) VALUES (:key, :value)"),
                        {"key": key, "value": value},
                    )

    @property
    def schema_version(self) -> int:
        return SCHEMA_VERSION

    def _upgrade_schema(self) -> None:
        """Add columns introduced after an installation's database was first created.

        `create_all` makes a missing table but never alters an existing one, so an
        installation that already holds engagements would otherwise fail on the first read.
        Each step adds one nullable column and is skipped once the column is there.
        """
        columns = {c["name"] for c in inspect(self.engine).get_columns("engagements")}
        added = {
            "occupation_group": "VARCHAR(40)",
            "license_key": "TEXT",
            "license_key_id": "VARCHAR(40)",
        }
        for name, kind in added.items():
            if name not in columns:
                with self.engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE engagements ADD COLUMN {name} {kind}"))

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

    # -- the firm ------------------------------------------------------------
    def firm(self) -> FirmRecord:
        with self._session() as db:
            row = db.get(_Firm, 1)
            return FirmRecord(row.name, row.logo, row.logo_mime) if row else FirmRecord()

    def _firm_row(self, db) -> _Firm:
        row = db.get(_Firm, 1)
        if row is None:
            row = _Firm(id=1, name="")
            db.add(row)
        return row

    def set_firm_name(self, name: str) -> FirmRecord:
        with self._session.begin() as db:
            self._firm_row(db).name = name.strip()
        return self.firm()

    def set_firm_logo(self, logo: bytes | None, mime: str | None) -> FirmRecord:
        """Keep a logo that has already been checked, or remove it with `None`."""
        with self._session.begin() as db:
            row = self._firm_row(db)
            row.logo, row.logo_mime = logo, mime
        return self.firm()

    # -- engagements ---------------------------------------------------------
    def create_engagement(
        self,
        *,
        name: str,
        client_name: str,
        sector: str,
        created_by: int,
        occupation_group: str | None = None,
        license_key: str | None = None,
        license_key_id: str | None = None,
    ) -> EngagementRecord:
        if sector not in SECTORS:
            raise ValueError(
                f"sector {sector!r} is not available. Cost models installed: {', '.join(SECTORS)}"
            )
        if not name.strip() or not client_name.strip():
            raise ValueError("an engagement needs a name and a client name")
        occupation_group = check_occupation_group(sector, occupation_group)
        with self._session.begin() as db:
            self._key_unused(db, license_key_id, engagement_id=None)
            row = _Engagement(
                id=secrets.token_hex(8),
                name=name.strip(),
                client_name=client_name.strip(),
                sector=sector,
                occupation_group=occupation_group,
                license_key=license_key,
                license_key_id=license_key_id,
                created_by=created_by,
            )
            db.add(row)
            db.flush()
            return _engagement(row)

    @staticmethod
    def _key_unused(db, license_key_id: str | None, engagement_id: str | None) -> None:
        """A key covers one engagement. Refuse one that another engagement already holds."""
        if not license_key_id:
            return
        holder = db.execute(
            select(_Engagement).where(_Engagement.license_key_id == license_key_id)
        ).scalar_one_or_none()
        if holder is not None and holder.id != engagement_id:
            raise ValueError(
                "this license key is already in use by another engagement in this "
                "installation. A key covers one engagement."
            )

    def set_license(
        self, engagement_id: str, license_key: str | None, license_key_id: str | None
    ) -> EngagementRecord:
        """Attach a key that has already been verified, replacing any the engagement had."""
        with self._session.begin() as db:
            row = db.get(_Engagement, engagement_id)
            if row is None:
                raise KeyError(engagement_id)
            self._key_unused(db, license_key_id, engagement_id)
            row.license_key, row.license_key_id = license_key, license_key_id
            db.flush()
            return _engagement(row)

    # -- the usage log -------------------------------------------------------
    def record_usage(
        self,
        event: str,
        *,
        engagement_id: str | None = None,
        key_id: str | None = None,
        detail: dict | None = None,
    ) -> None:
        """Append one entry, chained to the last. Counts only: never client HR data."""
        body = {
            "at": _now().replace(microsecond=0).isoformat(),
            "event": event,
            "engagement_id": engagement_id,
            "key_id": key_id,
            "detail": detail or {},
        }
        with self._session.begin() as db:
            last = db.execute(
                select(_Usage).order_by(_Usage.seq.desc()).limit(1)
            ).scalar_one_or_none()
            previous = last.hash if last else GENESIS
            seq = (last.seq if last else 0) + 1
            db.add(
                _Usage(
                    seq=seq,
                    at=body["at"],
                    event=event,
                    engagement_id=engagement_id,
                    key_id=key_id,
                    detail=json.dumps(body["detail"], sort_keys=True),
                    previous_hash=previous,
                    hash=entry_hash(previous, {"seq": seq, **body}),
                )
            )

    def usage_log(self) -> list[dict]:
        """Every entry, oldest first, in the form that is exported and verified."""
        with self._session() as db:
            rows = db.execute(select(_Usage).order_by(_Usage.seq)).scalars().all()
            return [
                {
                    "seq": r.seq,
                    "at": r.at,
                    "event": r.event,
                    "engagement_id": r.engagement_id,
                    "key_id": r.key_id,
                    "detail": json.loads(r.detail),
                    "previous_hash": r.previous_hash,
                    "hash": r.hash,
                }
                for r in rows
            ]

    def set_occupation_group(
        self, engagement_id: str, occupation_group: str | None
    ) -> EngagementRecord:
        """Change, or clear, the engagement's occupation group. Unlike the sector it can be
        changed later: it selects a multiplier, not a cost model."""
        with self._session.begin() as db:
            row = db.get(_Engagement, engagement_id)
            if row is None:
                raise KeyError(engagement_id)
            row.occupation_group = check_occupation_group(row.sector, occupation_group)
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
