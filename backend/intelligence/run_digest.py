"""Manual/cron entrypoint: python -m intelligence.run_digest --user-id 1

Dry-run is default. Never pass user tokens as CLI arguments. This process must
run in a trusted server environment with backend credentials and database access.
"""
import argparse
import asyncio
import os
from sqlmodel import Session
from database import engine
from models.portable_models import User
from .scheduled_reports import run_report


async def main():
    parser = argparse.ArgumentParser(description="Run one FinReview v2 digest check")
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--send", action="store_true", help="Opt-in to actual Telegram sending")
    args = parser.parse_args()
    if args.user_id < 1:
        parser.error("user-id must be positive")
    if args.send and os.getenv("FINREVIEW_V2_TELEGRAM_ENABLED", "").lower() != "true":
        parser.error("Live delivery is disabled. Set FINREVIEW_V2_TELEGRAM_ENABLED=true.")
    with Session(engine) as session:
        if session.get(User, args.user_id) is None:
            parser.error("user does not exist")
        result = await run_report(session, args.user_id, dry_run=not args.send)
        print(f"{result.state} slot={result.slot} date={result.trading_date}")
        if not args.send and result.message:
            print(result.message)


if __name__ == "__main__":
    asyncio.run(main())
