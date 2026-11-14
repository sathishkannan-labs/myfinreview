"""Fail-closed 09:00, 12:00, 15:00 IST digest preparation and delivery.

This is an operator-invoked worker, NOT an in-process background scheduler.
Every session date must be explicitly approved from an authoritative calendar.
Unknown days, network uncertainty, and duplicate invocations fail closed.
No credentials or real order placement are exposed.
"""
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from .models import NotificationDelivery
from .reporting import daily_digest
from .telegram import send_private_report

IST = ZoneInfo("Asia/Kolkata")
SLOTS = {"pre_market": (9, 0), "midday": (12, 0), "late_session": (15, 0)}


@dataclass(frozen=True)
class ReportResult:
    state: str
    slot: str
    trading_date: str
    message: str | None = None


def approved_sessions(text: str) -> set[date]:
    """Explicit exchange session calendar. Do not assume every weekday trades."""
    sessions: set[date] = set()
    for value in text.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            session = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Invalid FINREVIEW_TRADING_SESSIONS date") from exc
        if session.weekday() > 4:
            raise ValueError("Weekend date in approved session calendar")
        sessions.add(session)
    return sessions


def due_slot(now: datetime, *, sessions: set[date], tolerance_minutes=5) -> tuple[str, date] | None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Scheduling requires timezone-aware time")
    local = now.astimezone(IST)
    if local.date() not in sessions or local.weekday() > 4:
        return None
    for name, (hour, minute) in SLOTS.items():
        delta = (local.hour * 60 + local.minute) - (hour * 60 + minute)
        # Late-only window: never send upcoming digests early.
        if 0 <= delta < tolerance_minutes:
            return (name, local.date())
    return None


def expire_signals(session: Session, user_id: int, now: datetime) -> int:
    """Materialize expiration once; keep account isolation and event provenance."""
    from .models import StrategySignal, SignalEvent
    from sqlalchemy import update
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Expiration requires timezone-aware time")
    expired = session.exec(select(StrategySignal).where(
        StrategySignal.user_id == user_id,
        StrategySignal.status.in_(("WATCHING", "ACTIVE", "TRIGGERED")),
        StrategySignal.expires_at <= now).order_by(StrategySignal.id)).all()
    count = 0
    for signal in expired:
        original = signal.status
        result = session.exec(update(StrategySignal).where(
            StrategySignal.id == signal.id, StrategySignal.user_id == user_id,
            StrategySignal.status == original, StrategySignal.expires_at <= now
        ).values(status="EXPIRED"))
        if result.rowcount == 1:
            session.add(SignalEvent(user_id=user_id, signal_id=signal.id,
                                    old_status=original, new_status="EXPIRED",
                                    reason="Validity window ended", created_at=now))
            count += 1
    session.commit()
    return count


async def run_report(session: Session, user_id: int, *, now: datetime | None = None,
                     sessions: set[date] | None = None,
                     dry_run: bool = True, sender=None) -> ReportResult:
    """Dry-run has no DB writes and never sends. Live requires two explicit opt-ins.

    UNKNOWN means a sender may have accepted the message, so automatic retries
    are intentionally disabled until manual reconciliation.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Timezone required")
    now = now.astimezone(timezone.utc)
    if sessions is None:
        sessions = approved_sessions(os.getenv("FINREVIEW_TRADING_SESSIONS", ""))
    due = due_slot(now, sessions=sessions)
    local_day = now.astimezone(IST).date().isoformat()
    if due is None:
        return ReportResult("NOT_DUE_OR_CALENDAR_UNVERIFIED", "", local_day)
    slot, trading_date = due
    message = daily_digest(session, user_id, slot, now=now)
    if dry_run:
        return ReportResult("DRY_RUN", slot, trading_date.isoformat(), message)
    if os.getenv("FINREVIEW_V2_TELEGRAM_ENABLED", "").lower() != "true":
        return ReportResult("DISABLED", slot, trading_date.isoformat())
    key = f"telegram-digest-v1:{trading_date.isoformat()}:{slot}"
    existing = session.exec(select(NotificationDelivery).where(
        NotificationDelivery.user_id == user_id,
        NotificationDelivery.dedupe_key == key)).first()
    if existing:
        return ReportResult("ALREADY_CLAIMED", slot, trading_date.isoformat())
    entry = NotificationDelivery(user_id=user_id, dedupe_key=key,
                                 status="CLAIMED", created_at=now)
    session.add(entry)
    try:
        session.commit()  # unique constraint arbitrates concurrent delivery claims
    except IntegrityError:
        session.rollback()
        return ReportResult("ALREADY_CLAIMED", slot, trading_date.isoformat())
    session.refresh(entry)
    try:
        await (sender or send_private_report)(message)
    except Exception:
        # A timeout may have occurred after Telegram accepted the payload.
        # Do not auto-retry and do not store exception text (may include token).
        entry.status = "UNKNOWN"
        entry.error = "Delivery outcome unverified; manual reconciliation required"
        session.add(entry)
        session.commit()
        return ReportResult("UNKNOWN", slot, trading_date.isoformat())
    entry.status = "SENT"
    entry.delivered_at = datetime.now(timezone.utc)
    session.add(entry)
    session.commit()
    return ReportResult("SENT", slot, trading_date.isoformat())
