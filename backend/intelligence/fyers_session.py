"""Opt-in FYERS third-party connections scoped to a FinReview login."""
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

from sqlalchemy import delete, update
from sqlmodel import Field, Session, SQLModel, select

from .broker_oauth import BrokerAuthError, broker_settings
from .credential_vault import _cipher, _utc_expiry


class FyersSession(SQLModel, table=True):
    __tablename__ = "v2_fyers_sessions"
    session_key: str = Field(primary_key=True, max_length=64)
    user_id: int = Field(foreign_key="user.id", index=True)
    state_hash: str = Field(unique=True, index=True, max_length=64)
    expires_at: datetime = Field(index=True)
    claimed: bool = False
    encrypted_credentials: str | None = None


def enabled():
    """Third-party onboarding is explicitly opt-in AND approval-gated.

    The approval flag is an operator attestation, never proof of FYERS consent.
    Until FYERS grants authorization, both settings must remain false.
    """
    return (
        os.getenv("FINREVIEW_FYERS_THIRD_PARTY_ENABLED", "").lower() == "true"
        and os.getenv("FINREVIEW_FYERS_THIRD_PARTY_APPROVED", "").lower() == "true"
    )


def configuration_ready():
    """Safe capability check: no secrets or configuration errors in the response."""
    if not enabled():
        return False
    try:
        _, _, callback = broker_settings("fyers")
        parsed = urlsplit(callback)
        # Accessing .port validates both its syntax and numeric range.
        parsed.port
        if (parsed.scheme != "https" or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment):
            return False
        return_url()
        _cipher()
        return True
    except (BrokerAuthError, ValueError, RuntimeError):
        return False


def return_url():
    value = os.getenv("FINREVIEW_FYERS_FRONTEND_RETURN_URL", "")
    try:
        url = urlsplit(value)
        if (url.scheme == "https" and url.hostname and not url.username and
                url.password is None and not url.query and not url.fragment and
                url.path.endswith("/intelligence.html") and
                not any(c.isspace() or c == "\\" for c in value)):
            return value + "#fyersConnectionPanel"
    except ValueError:
        pass
    raise BrokerAuthError("FYERS return URL is unavailable")


def _key(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def session_key(authorization):
    if not isinstance(authorization, str):
        raise BrokerAuthError("Sign in again to connect FYERS")
    token = authorization.removeprefix("Bearer ").strip()
    if not token.startswith("frv2:"):
        raise BrokerAuthError("Sign in again to connect FYERS")
    return _key(token)


def begin(db: Session, user_id: int, authorization: str):
    if not enabled():
        raise BrokerAuthError("FYERS third-party integration is not approved and enabled")
    key = session_key(authorization)
    client_id, _, redirect = broker_settings("fyers")
    return_url()
    _cipher()
    now = datetime.now(timezone.utc)
    db.exec(delete(FyersSession).where(FyersSession.expires_at <= now))
    row = db.get(FyersSession, key)
    if row is not None and not row.claimed:
        db.rollback()
        raise BrokerAuthError("Finish or disconnect the pending FYERS connection")
    if row is not None:
        db.delete(row)
        db.flush()
    state = secrets.token_urlsafe(32)
    db.add(FyersSession(session_key=key, user_id=user_id, state_hash=_key(state),
                        expires_at=now + timedelta(minutes=5)))
    db.commit()
    query = urlencode({"response_type": "code", "client_id": client_id,
                       "redirect_uri": redirect, "state": state})
    return {"authorization_url": f"https://api-t1.fyers.in/api/v3/generate-authcode?{query}",
            "provider": "fyers", "expires_in_seconds": 300, "status": "PENDING", "no_real_orders": True}


def claim(db: Session, state: str):
    if not enabled():
        raise BrokerAuthError("FYERS third-party integration is not approved and enabled")
    if not isinstance(state, str) or not 20 <= len(state) <= 200:
        raise BrokerAuthError("Invalid FYERS authorization")
    digest = _key(state)
    result = db.exec(update(FyersSession).where(
        FyersSession.state_hash == digest,
        FyersSession.claimed.is_(False),
        FyersSession.expires_at > datetime.now(timezone.utc),
    ).values(claimed=True))
    db.commit()
    if result.rowcount != 1:
        raise BrokerAuthError("Invalid FYERS authorization")
    return digest


def finish(db: Session, digest: str, credentials: dict, expiry: datetime):
    row = db.exec(select(FyersSession).where(FyersSession.state_hash == digest)).first()
    if row is None:
        raise BrokerAuthError("FYERS connection was canceled")
    payload = json.dumps({"session_key": row.session_key, "credentials": credentials})
    encrypted = _cipher().encrypt(payload.encode("utf-8")).decode("ascii")
    result = db.exec(update(FyersSession).where(
        FyersSession.state_hash == digest,
        FyersSession.claimed.is_(True),
        FyersSession.encrypted_credentials.is_(None),
        FyersSession.expires_at > datetime.now(timezone.utc),
    ).values(encrypted_credentials=encrypted, expires_at=expiry))
    db.commit()
    if result.rowcount != 1:
        raise BrokerAuthError("FYERS connection was canceled")


def _find(db: Session, user_id: int, authorization: str | None):
    row = db.get(FyersSession, session_key(authorization))
    return row if row and row.user_id == user_id else None


def status(db: Session, user_id: int, authorization: str | None):
    try:
        row = _find(db, user_id, authorization)
    except BrokerAuthError:
        row = None
    records = []
    if row is not None and row.encrypted_credentials:
        expiry = _utc_expiry(row.expires_at)
        records = [{"provider": "fyers", "purpose": purpose,
                    "state": "EXPIRED" if expiry <= datetime.now(timezone.utc) else "STORED_UNVERIFIED",
                    "expires_at": expiry.isoformat()} for purpose in ("historical", "quotes")]
    return {"connections": records, "oauth_configured": True}


def read(db: Session, user_id: int, authorization: str | None):
    if not enabled():
        raise BrokerAuthError("FYERS third-party integration disabled")
    row = _find(db, user_id, authorization)
    if row is None or not row.encrypted_credentials or _utc_expiry(row.expires_at) <= datetime.now(timezone.utc):
        raise BrokerAuthError("FYERS connection expired or unavailable")
    payload = json.loads(_cipher().decrypt(row.encrypted_credentials.encode("ascii")))
    if payload["session_key"] != row.session_key:
        raise BrokerAuthError("FYERS connection scope invalid")
    return payload["credentials"]


def disconnect(db: Session, user_id: int, authorization: str | None):
    try:
        key = session_key(authorization)
    except BrokerAuthError:
        return
    db.exec(delete(FyersSession).where(FyersSession.session_key == key, FyersSession.user_id == user_id))
    db.commit()
