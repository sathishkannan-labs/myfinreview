"""Conservative broker session cutoffs in Asia/Kolkata.

These are *upper bounds*, not a promise the broker will honor a session up
to cutoff; manual revocation and provider errors always take precedence.
"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
IST=ZoneInfo("Asia/Kolkata")
CUTOFF={"fyers":time(6,30),"upstox":time(3,30),"zerodha":time(6,0)}

def conservative_expiry(provider: str, issued_at: datetime) -> datetime:
    if provider not in CUTOFF:
        raise ValueError("Unsupported broker")
    if issued_at.tzinfo is None or issued_at.utcoffset() is None:
        raise ValueError("Explicit timezone required")
    local=issued_at.astimezone(IST)
    cutoff=datetime.combine(local.date(),CUTOFF[provider],IST)
    if local>=cutoff:
        cutoff+=timedelta(days=1)
    # Proactively stop just before the broker's scheduled daily expiry.
    return (cutoff-timedelta(minutes=5)).astimezone(timezone.utc)
