"""Deterministic end-of-bar research signals (not buy/sell instructions).

Consumes only validated, chronologically ordered *completed* candles.
Only a downstream calendar-aware worker may decide that a bar is complete.
No forward-looking features, ML-generated probabilities, or broker orders.
"""
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from .market_data import Candle, MarketDataError, validate_candles

@dataclass(frozen=True)
class StrategyObservation:
    kind: str
    status: str
    as_of: datetime
    price: float
    trigger: float | None
    invalidation: float | None
    atr: float | None
    rationale: str

def _checked(bars: list[Candle]):
    checked = validate_candles([b.as_json() for b in bars])
    if [b.timestamp for b in bars] != [b.timestamp for b in checked]:
        raise MarketDataError("Candles must be chronological and unique")
    return checked

def atr_at_end(bars: list[Candle], period: int = 14):
    if period < 2 or period > 100:
        raise ValueError("Unsupported ATR window")
    checked = _checked(bars)
    if len(checked) < period + 1:
        return None
    tr = []
    for before, current in zip(checked[:-1], checked[1:]):
        tr.append(max(current.high - current.low,
                      abs(current.high - before.close),
                      abs(current.low - before.close)))
    return mean(tr[-period:])

def breakout_observation(bars: list[Candle], *, lookback=20, atr_period=14):
    if not 5 <= lookback <= 250:
        raise ValueError("Breakout lookback out of range")
    checked = _checked(bars)
    if len(checked) < max(lookback + 1, atr_period + 1):
        return None
    current = checked[-1]
    # Critically exclude the current candle from the historical breakout threshold.
    prior_high = max(b.high for b in checked[-lookback-1:-1])
    atr = atr_at_end(checked, period=atr_period)
    crossed = current.close > prior_high
    invalidation = max(0.01, current.close - 2 * atr) if crossed and atr else None
    return StrategyObservation(
        kind="close_above_prior_high", status="WATCHING" if not crossed else "CANDIDATE",
        as_of=current.timestamp, price=current.close, trigger=prior_high,
        invalidation=invalidation, atr=atr,
        rationale=(f"Completed close {current.close:.2f} {'exceeded' if crossed else 'did not exceed'} "
                   f"prior {lookback}-bar high {prior_high:.2f}; research candidate only"))

def sma_trend_observation(bars: list[Candle], *, fast=20, slow=50):
    if not 2 <= fast < slow <= 250:
        raise ValueError("SMA windows out of range")
    checked = _checked(bars)
    if len(checked) < slow:
        return None
    values = [b.close for b in checked]
    a, b = mean(values[-fast:]), mean(values[-slow:])
    return StrategyObservation(
        kind="sma_trend", status="CANDIDATE" if a > b else "WATCHING",
        as_of=checked[-1].timestamp, price=values[-1], trigger=a,
        invalidation=None, atr=None,
        rationale=f"Fast SMA={a:.2f}, slow SMA={b:.2f}. Trend filter only; not a return probability.")
