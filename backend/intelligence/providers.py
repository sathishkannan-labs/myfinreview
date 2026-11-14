"""Bring-your-own-provider historical data interface and registry."""
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo
from .market_data import Candle, MarketDataError, validate_candles, validate_range
from .upstox_data import UpstoxReadOnlyData
from .fyers_data import FyersReadOnlyData
from .zerodha_data import ZerodhaReadOnlyData

@dataclass(frozen=True)
class HistoricalDataset:
    provider: str
    instrument: str
    interval: str
    adjustment: str
    candles: tuple[Candle, ...]
    def as_json(self):
        return {"provider": self.provider, "instrument": self.instrument,
                "interval": self.interval, "adjustment": self.adjustment,
                "count": len(self.candles),
                "candles": [c.as_json() for c in self.candles]}

def available_providers():
    return {"upstox": bool(os.getenv("UPSTOX_ANALYTICS_TOKEN")),
            "fyers": bool(os.getenv("FYERS_CLIENT_ID") and os.getenv("FYERS_ACCESS_TOKEN")),
            "zerodha": bool(os.getenv("ZERODHA_API_KEY") and os.getenv("ZERODHA_ACCESS_TOKEN")),
            "upstox_quotes": bool(os.getenv("UPSTOX_ACCESS_TOKEN")),
            "csv": True}

async def load_history(provider: str, instrument: str, start: date, end: date, *,
                       upstox=None, fyers=None, zerodha=None):
    """Retrieve daily bars without exposing tokens, or mixing adjustments."""
    validate_range(start, end, max_days=3650)
    if provider == "upstox":
        client = upstox or UpstoxReadOnlyData()
        candles = await client.history(instrument, start, end)
    elif provider == "fyers":
        if not instrument or len(instrument) > 80 or ":" not in instrument:
            raise MarketDataError("Use an official FYERS exchange:symbol")
        client = fyers or FyersReadOnlyData()
        # Historical API uses UNIX seconds; FYERS timestamps are UTC.
        from_epoch = int(datetime.combine(start, time.min, ZoneInfo('Asia/Kolkata')).timestamp())
        to_epoch = int(datetime.combine(end+timedelta(days=1), time.min, ZoneInfo('Asia/Kolkata')).timestamp())-1
        payload = await client.history(instrument, "D", from_epoch, to_epoch)
        rows = payload.get("candles")
        if not isinstance(rows, list):
            raise MarketDataError("FYERS returned malformed candles")
        candles = [c for c in validate_candles(rows) if start <= c.timestamp.astimezone(ZoneInfo('Asia/Kolkata')).date() <= end]
    elif provider == "zerodha":
        client = zerodha or ZerodhaReadOnlyData()
        candles = await client.history(instrument, start, end)
    else:
        raise MarketDataError("Unsupported provider; choose FYERS, Upstox or Zerodha")
    # We cannot assume corporate-action adjustment across provider feeds.
    return HistoricalDataset(provider, instrument, "1d", "provider_raw_unverified",
                             tuple(candles))
