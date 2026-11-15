"""Trusted staging-only broker smoke check.

Example from backend/:
  FINREVIEW_ENV=staging FINREVIEW_V2_BROKER_PROBES_ENABLED=true \\
  python -m intelligence.staging_smoke --live --user-id 1 --broker fyers \\
  --historical-instrument NSE:INFY-EQ --quote-instrument NSE:INFY-EQ \\
  --start 2026-11-10 --end 2026-11-11

Secrets must already be in the encrypted vault, inserted through a vetted
auth flow. No broker passwords, tokens, prices or account identifiers printed.
No real trading and no write to price cache.
"""
import argparse
import asyncio
import os
from datetime import date
from sqlmodel import Session
from database import engine
from models.portable_models import User
from .market_smoke import check_stored_market_access

def configure_parser():
    p=argparse.ArgumentParser(description="Read-only broker integration smoke check")
    p.add_argument("--live",action="store_true",help="Explicit opt-in to broker HTTP calls")
    p.add_argument("--user-id",type=int,required=True)
    p.add_argument("--broker",choices=["fyers","upstox","zerodha"],required=True)
    p.add_argument("--historical-instrument",required=True)
    p.add_argument("--quote-instrument",required=True)
    p.add_argument("--start",type=date.fromisoformat,required=True)
    p.add_argument("--end",type=date.fromisoformat,required=True)
    return p

def permit_live(*, live: bool, environment: str, enabled: str):
    if not live or environment!="staging" or enabled.lower()!="true":
        raise ValueError("Private staging-only diagnostics are not enabled")

async def run(args):
    permit_live(live=args.live,environment=os.getenv("FINREVIEW_ENV",""),
                enabled=os.getenv("FINREVIEW_V2_BROKER_PROBES_ENABLED","false"))
    if args.user_id<=0:
        raise ValueError("Invalid user id")
    with Session(engine) as session:
        if session.get(User,args.user_id) is None:
            raise ValueError("User not found")
        return await check_stored_market_access(
            session,args.user_id,args.broker,
            history_instrument=args.historical_instrument,
            quote_instrument=args.quote_instrument,start=args.start,end=args.end)

def main():
    args=configure_parser().parse_args()
    try:
        result=asyncio.run(run(args))
    except Exception:
        # Never echo HTTP/client exceptions or credentials to terminal logs.
        print("UNAVAILABLE: private staging diagnostics could not complete")
        raise SystemExit(2)
    print(f"Broker={result.broker} profile={result.profile} "
          f"history={result.history} bars={result.history_bars} quotes={result.quotes} "
          "trading=DISABLED")
    if result.profile!="AUTHENTICATED" or result.history!="VERIFIED_SAMPLE" or result.quotes!="VERIFIED_SAMPLE":
        raise SystemExit(1)

if __name__=="__main__":
    main()
