"""No-broker CSV research: deterministic, read-only, no persistence.

Accept only user-supplied, timestamped, completed historical daily OHLCV.
Never infer data licensing, provider correctness, return forecasts or trades.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from .market_data import parse_csv_history, MarketDataError
from .strategies import breakout_observation, sma_trend_observation

IST=ZoneInfo("Asia/Kolkata")

def analyze_csv_bytes(content: bytes, *, now=None) -> dict:
    bars=parse_csv_history(content)
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise MarketDataError("Timezone-aware reference required")
    local_today=now.astimezone(IST).date()
    if any(b.timestamp.astimezone(IST).date() >= local_today for b in bars):
        raise MarketDataError("CSV contains future or current-day candles; use completed historical days only")
    if not bars:
        raise MarketDataError("CSV has no candles")
    if len({b.timestamp.astimezone(IST).date() for b in bars}) != len(bars):
        raise MarketDataError("CSV contains multiple candles on one date; daily bars only")
    # Dates are intentionally NOT filled: the exchange holiday calendar is not validated.
    def result(obs):
        if obs is None:
            return {"status":"INSUFFICIENT_DATA","observation":None}
        return {"status":"READY","observation":{
            "kind":obs.kind,"state":obs.status,"as_of":obs.as_of.isoformat(),
            "reference_price":obs.price,"trigger":obs.trigger,
            "invalidation":obs.invalidation,"atr":obs.atr,
            "rationale":obs.rationale}}
    return {
        "status":"READY" if len(bars)>=50 else "INSUFFICIENT_DATA",
        "source":"USER_UPLOADED_CSV","stored":False,"trading_enabled":False,
        "provider_authenticated":False,"execution_mode":"RESEARCH_ONLY",
        "adjustment":"UNVERIFIED","exchange_calendar":"NOT_VERIFIED",
        "rows":len(bars),"first_timestamp":bars[0].timestamp.isoformat(),
        "last_timestamp":bars[-1].timestamp.isoformat(),
        "last_close":bars[-1].close,
        "strategies":{
            "breakout":result(breakout_observation(bars)),
            "sma_trend":result(sma_trend_observation(bars))
        }
    }
