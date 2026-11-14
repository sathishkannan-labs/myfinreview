"""Opt-in, operator-invoked historical sync and *watchlist-only* research pipeline.

No scheduler, real broker orders or Telegram delivery here. Signals remain
WATCHING, never ACTIVE, until an independently validated live rule confirms them.
"""
import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlmodel import Session, select

from .providers import load_history
from .candle_cache import save_candles, read_candles
from .market_data import MarketDataError
from .strategies import breakout_observation, sma_trend_observation
from .service import create_signal
from .models import StrategySignal

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class ResearchResult:
    provider: str
    instrument: str
    inserted: int
    unchanged: int
    bars_used: int
    outcome: str
    signal_id: int | None = None
    source_bar_at: str | None = None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MarketDataError("An aware UTC processing time is required")
    return value.astimezone(timezone.utc)


def _closed_history(session: Session, user_id: int, provider: str, instrument: str, now: datetime):
    # Daily bars for the current Indian calendar day may still be incomplete.
    end = now
    start = now - timedelta(days=450)
    history = read_candles(session, user_id, provider, instrument, start, end)
    today = now.astimezone(IST).date()
    return [b for b in history["bars"] if b.timestamp.astimezone(IST).date() < today]


def evaluate_cached_research(session: Session, user_id: int, provider: str, instrument: str,
                             *, now: datetime | None = None, persist: bool = False) -> ResearchResult:
    """Conservative research candidates, never actionable buy recommendations.

    A previous-day breakout is a WATCHING observation, not an executable order.
    No holiday-calendar-aware confirmation exists yet.
    """
    now = _ensure_utc(now or datetime.now(timezone.utc))
    if provider not in {"upstox", "fyers"}:
        raise MarketDataError("Unsupported research provider")
    bars = _closed_history(session, user_id, provider, instrument, now)
    if len(bars) < 51:
        return ResearchResult(provider, instrument, 0, 0, len(bars), "INSUFFICIENT_DATA")
    last = bars[-1]
    age_days = (now.astimezone(IST).date() - last.timestamp.astimezone(IST).date()).days
    if age_days < 1 or age_days > 4:
        return ResearchResult(provider, instrument, 0, 0, len(bars), "STALE_DATA",
                              source_bar_at=last.timestamp.isoformat())
    breakout = breakout_observation(bars)
    trend = sma_trend_observation(bars)
    if breakout is None or trend is None:
        return ResearchResult(provider, instrument, 0, 0, len(bars), "INSUFFICIENT_DATA")
    if breakout.status != "CANDIDATE" or trend.status != "CANDIDATE":
        return ResearchResult(provider, instrument, 0, 0, len(bars), "NO_CANDIDATE",
                              source_bar_at=last.timestamp.isoformat())
    if not persist:
        return ResearchResult(provider, instrument, 0, 0, len(bars), "CANDIDATE_PREVIEW",
                              source_bar_at=last.timestamp.isoformat())
    local = now.astimezone(IST)
    # Guardrails: research alert only during normal weekday monitoring; exchange
    # holidays still need an authoritative calendar before production scheduling.
    if local.weekday() > 4 or not (9 <= local.hour < 15):
        return ResearchResult(provider, instrument, 0, 0, len(bars), "OUTSIDE_MONITORING_WINDOW",
                              source_bar_at=last.timestamp.isoformat())
    digest = hashlib.sha256(
        f"v2-research-v1|{provider}|{instrument}|{last.timestamp.isoformat()}".encode()
    ).hexdigest()[:32]
    key = "research-v1-" + digest
    old = session.exec(select(StrategySignal).where(
        StrategySignal.user_id == user_id, StrategySignal.source_key == key)).first()
    if old:
        return ResearchResult(provider, instrument, 0, 0, len(bars), "ALREADY_RECORDED",
                              signal_id=old.id, source_bar_at=last.timestamp.isoformat())
    # This field is the time the research observation was generated, not the
    # underlying bar timestamp (which is explicitly recorded in rationale).
    reason = (f"PRELIMINARY daily candle observation only. Completed source bar "
              f"{last.timestamp.isoformat()}. {breakout.rationale}. {trend.rationale}. "
              "Requires fresh market confirmation before considering a trade.")
    signal = create_signal(
        session, user_id, symbol=instrument, strategy="daily_breakout_sma_research_v1",
        source_key=key, reference_price=last.close, ttl_minutes=60,
        rationale=reason[:1600], data_timestamp=now, entry_price=None,
        invalidation_price=breakout.invalidation)
    return ResearchResult(provider, instrument, 0, 0, len(bars), "WATCHING_CREATED",
                          signal_id=signal.id, source_bar_at=last.timestamp.isoformat())


async def synchronize_and_research(session: Session, user_id: int, provider: str, instrument: str,
                                   start: date, end: date, *,
                                   now: datetime | None = None, client=None,
                                   publish_watch: bool = False) -> ResearchResult:
    """Ingest provider history and optionally record non-actionable watchlist signals.

    The caller must independently verify data provider licensing and credentials.
    Failure on invalid/revised candles aborts the operation. A repeat run with
    identical data is idempotent; existing cache data is never overwritten.
    """
    now = _ensure_utc(now or datetime.now(timezone.utc))
    opts = {"upstox": client} if provider == "upstox" else {"fyers": client}
    dataset = await load_history(provider, instrument, start, end, **opts)
    if not dataset.candles:
        return ResearchResult(provider, instrument, 0, 0, 0, "NO_PROVIDER_DATA")
    counts = save_candles(session, user_id, provider, instrument, list(dataset.candles),
                          adjustment=dataset.adjustment)
    result = evaluate_cached_research(session, user_id, provider, instrument,
                                      now=now, persist=publish_watch)
    return ResearchResult(provider, instrument, counts["inserted"], counts["unchanged"],
                          result.bars_used, result.outcome, result.signal_id,
                          result.source_bar_at)
