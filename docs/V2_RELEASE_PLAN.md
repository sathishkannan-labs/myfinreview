# FinReview 2.0 release candidate: validation gates

Development branch: `release/2.x`. The public `main` branch and existing hosted app remain unchanged.

## What has been implemented
- Additive SQLModel signal/event, paper-account/trade and notification-delivery tables.
- Time-bound signal state machine; expiration is checked on reads even when workers are down.
- Account-isolated read-only `/v2/signals`, `/v2/paper-account`, `/v2/status` endpoints.
- Optional paper account initialization. No browser endpoints for posting research signals or synthetic fills.
- FYERS **market-data-only** client (quotes and candles), requiring separately configured non-trading app access.
- Provider-neutral OHLCV validation, Upstox V3 daily historical adapter, FYERS daily history normalization, and CSV validation-only import.
- Protected provider status and historical research routes (disabled by default on public deployments).
- Account-isolated, source-attributed historical daily candle cache; exact duplicate imports are idempotent and revised source data are rejected pending reconciliation.
- Deterministic SMA, ATR and close-based breakout observations using completed daily candles only, with no automatic order placement.
- Authenticated read-only `/v2/market/analyze-cached` returns research observations or clear NO_DATA/INSUFFICIENT_DATA states.
- [Provider setup documentation](V2_MARKET_DATA.md) including BYO tokens, privacy and licensing limitations.
- Telegram HTTP sender and deterministic digest formatter. Neither is scheduled or activated by default.
- Separate read-only dashboard preview at `frontend/intelligence.html`.
- CI regression test additions and v2-specific unit tests.

## Not yet implemented (blocks v2.0.0 final release)
- Broker authentication handoff, approved session renewal, and live FYERS/Upstox connectivity and permissions testing.
- Market calendar-aware 09:00/12:00/15:00 reporting worker and private Telegram delivery scheduling.
- End-to-end verified FYERS/Upstox ingestion into cache, strategy job orchestration, persisted signals with immutable strategy versions and exchange-calculated expiry.
- Authorised simulation worker, future-data-safe fill modelling, exits, fees and benchmark reports.
- Production-grade migration with rollback and database backup validation.
- Integrated navigation and finished user interface for the existing Community dashboard.
- Security, market-data licence/redistribution and external deployment review.
- Complete GitHub Actions checks on the final commit plus independent staging verification.

## Deployment defaults
Do not set trading-capable FYERS tokens. Never commit broker or Telegram credentials.
No real broker order endpoint exists. All sample trading is simulated.
The new endpoints require existing FinReview bearer authentication.

## Release rules
Do not create `v2.0.0` tag or GitHub Release until all blockers are addressed, CI passes, staging is reviewed and documentation is complete. A tag should point at the exact validated commit on `release/2.x`; merging into `main` is not necessary.
