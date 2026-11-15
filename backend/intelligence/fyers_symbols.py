"""FYERS NSE cash equity symbol catalog for owner-only personal research.

Official provider Symbol Master: fixed public host, no user-controlled URL,
bounded download, in-process TTL, no access-token forwarding, no market quotes.
Only current NSE EQ tickers are selectable; no futures/options/indices.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import re
import httpx

MASTER_URL="https://public.fyers.in/sym_details/NSE_CM_sym_master.json"
MAX_BYTES=18_000_000
TTL=timedelta(hours=6)
TICKER=re.compile(r"^NSE:[A-Z0-9][A-Z0-9&._-]{0,65}-EQ$")
_cache={}
_fetched_at=None
_lock=asyncio.Lock()

def parse_master(payload):
    if not isinstance(payload,dict):
        raise ValueError("Invalid symbol master response")
    out={}
    for symbol,entry in payload.items():
        if not isinstance(symbol,str) or not TICKER.fullmatch(symbol) or not isinstance(entry,dict):
            continue
        # Nontrading master is for discovery, not market-data entitlement.
        if entry.get("tradeStatus") not in (1,"1",True):
            continue
        display=""
        for field in ("symDetails","symbolDesc","exSymName","exSymbol","description","name","shortName","fullName","symbolDescription"):
            v=entry.get(field)
            if isinstance(v,str) and v.strip():
                display=v.strip()[:110]
                break
        out[symbol]=display or symbol.split(":",1)[1].removesuffix("-EQ")
    if not out:
        raise ValueError("No supported NSE EQ instruments in symbol master")
    return out

async def catalog(*,client=None,now=None):
    global _cache,_fetched_at
    now=now or datetime.now(timezone.utc)
    if _cache and _fetched_at and now-_fetched_at<TTL:
        return _cache
    async with _lock:
        if _cache and _fetched_at and now-_fetched_at<TTL:
            return _cache
        own=client is None
        http=client or httpx.AsyncClient(timeout=15.0,follow_redirects=False)
        try:
            # Streaming limits prohibit unbounded responses.
            async with http.stream("GET",MASTER_URL,headers={"Accept":"application/json"}) as response:
                response.raise_for_status()
                chunks=[];size=0
                async for chunk in response.aiter_bytes():
                    size+=len(chunk)
                    if size>MAX_BYTES:
                        raise ValueError("FYERS symbol master exceeds safe limit")
                    chunks.append(chunk)
            import json
            parsed=parse_master(json.loads(b"".join(chunks)))
            _cache=parsed
            _fetched_at=now
            return parsed
        finally:
            if own:
                await http.aclose()

async def search_symbols(query,*,limit=15):
    q=query.strip().upper()
    if len(q)<2 or len(q)>64 or not re.fullmatch(r"[A-Z0-9 &.:-]+",q):
        return []
    items=await catalog()
    needle=q.replace("NSE:","")
    exact=[];prefix=[];other=[]
    for symbol,name in items.items():
        code=symbol.split(":",1)[1].removesuffix("-EQ")
        label=name.upper()
        if code==needle:exact.append((symbol,name))
        elif code.startswith(needle) or label.startswith(needle):prefix.append((symbol,name))
        elif needle in code or needle in label:other.append((symbol,name))
    found=sorted(exact)+sorted(prefix)+sorted(other)
    return [{"symbol":key,"name":value} for key,value in found[:limit]]

async def is_current_equity(symbol):
    if not isinstance(symbol,str) or not TICKER.fullmatch(symbol):
        return False
    master=await catalog()
    return symbol in master
