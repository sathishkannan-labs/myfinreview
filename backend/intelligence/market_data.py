"""Validated, provider-neutral historical OHLCV data for research only.

The normalized response retains source identity and timestamps. Do not
interpret raw prices as corporate-action adjusted. Never place orders.
"""
import csv
import io
import math
import re
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from typing import Iterable

class MarketDataError(ValueError):
    pass

@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def as_json(self):
        result = asdict(self)
        result["timestamp"] = self.timestamp.isoformat()
        return result

def parse_timestamp(value):
    if isinstance(value, (int, float)):
        stamp = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str):
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        stamp = value
    else:
        raise MarketDataError("Invalid candle timestamp")
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise MarketDataError("Candle timestamp requires an explicit timezone")
    return stamp.astimezone(timezone.utc)

def validate_candles(rows: Iterable, *, max_rows=50000):
    """Reject duplicates, invalid OHLC/volume, and inconsistent timestamps.

    Sorting is allowed because many providers return reverse chronological data.
    We deliberately do NOT fill missing candles or assume exchange holidays.
    """
    result = []
    seen = set()
    for row in rows:
        if len(result) >= max_rows:
            raise MarketDataError("Historical request exceeds row limit")
        try:
            if isinstance(row, dict):
                stamp = parse_timestamp(row["timestamp"])
                values = [float(row[k]) for k in ("open", "high", "low", "close", "volume")]
            else:
                stamp = parse_timestamp(row[0])
                values = list(map(float, row[1:6]))
        except (KeyError, ValueError, TypeError, OverflowError, IndexError) as exc:
            raise MarketDataError("Malformed OHLCV record") from exc
        o, h, l, c, v = values
        if (not all(math.isfinite(x) for x in values) or
            min(o, h, l, c) <= 0 or v < 0 or
            h < max(o, c, l) or l > min(o, c, h)):
            raise MarketDataError("Invalid OHLCV price or volume")
        if stamp in seen:
            raise MarketDataError("Duplicate candle timestamp")
        seen.add(stamp)
        result.append(Candle(stamp, o, h, l, c, v))
    result.sort(key=lambda x: x.timestamp)
    return result

def parse_csv_history(content: bytes, *, max_bytes=3_000_000):
    if not content or len(content) > max_bytes:
        raise MarketDataError("CSV must be nonempty and at most 3 MB")
    try:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise MarketDataError("CSV headers: timestamp,open,high,low,close,volume")
        rows = []
        for row in reader:
            rows.append(row)
            if len(rows) > 50000:
                raise MarketDataError("CSV row limit exceeded")
    except UnicodeError as exc:
        raise MarketDataError("CSV must be UTF-8") from exc
    return validate_candles(rows)

def validate_range(start: date, end: date, *, max_days=3660):
    if end < start or (end-start).days > max_days:
        raise MarketDataError("Invalid or excessive historical date range")

def instrument_key(value: str):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_]+\|[A-Za-z0-9_.-]+", value):
        raise MarketDataError("Use an official exchange|instrument_key")
    return value
