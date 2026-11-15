"""Deterministic formatting for scheduled private research digests."""
from datetime import datetime, timezone
from .service import list_signals

def daily_digest(session, user_id, report_name, now=None):
    now = now or datetime.now(timezone.utc)
    active = list_signals(session, user_id, active_only=True)
    title = {"pre_market": "09:00 pre-market", "midday": "12:00 midday",
             "late_session": "15:00 late session"}.get(report_name)
    if title is None:
        raise ValueError("Unknown scheduled report")
    rows = [f"FinReview 2.0 | {title}", f"Snapshot UTC: {now.isoformat()}",
            f"Live research signals: {len(active)}"]
    for signal in active[:8]:
        rows.append(f"• {signal['symbol']} / {signal['strategy']} / "
                    f"{signal['effective_status']} / expires {signal['expires_at']} UTC")
    rows.append("Research only; verify prices and decide manually. No broker orders.")
    return "\n".join(rows)
