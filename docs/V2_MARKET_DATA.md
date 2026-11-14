# FinReview 2.x — bring your own historical market data (BYO API)

This document describes **development-stage** adapters on `release/2.x`. The public production demo running from `main` is unchanged. Nothing here enables real-money orders.

## Provider status

| Provider | Data type | Implementation status | Key requirement |
| --- | --- | --- | --- |
| Upstox V3 | Historical daily OHLCV | Implemented, mocked CI tested; live unverified | User-supplied read-only Analytics Token |
| FYERS V3 Standard | Historical daily OHLCV + quote adapter | Implemented, not live tested | User-supplied non-trading session |
| CSV | UTC/offset-stamped OHLCV validation | Implemented, mocked CI tested | User-owned, licensed dataset |
| Yahoo Finance | Existing v1 portfolio pricing (separate code) | Existing, not upgraded to v2 provider contract | Verify Yahoo usage restrictions |

Upstox docs: https://upstox.com/developer/api-documentation/
FYERS docs: https://fyers.in/api-docs/

## Private deployment configuration

Do not put credentials in Git, screenshots, README examples, Telegram, frontend JS or public deployments.

```dotenv
FINREVIEW_V2_MARKET_API_ENABLED=false
UPSTOX_ANALYTICS_TOKEN=
FYERS_CLIENT_ID=
FYERS_ACCESS_TOKEN=
```

The new `/v2/market/history` route is **off by default** because a public web API that uses one server-side token could become an unauthorized third-party exchange-data proxy. Enable only when the deployment is private, authenticated, usage-limited, and permitted by provider terms. A public multi-user deployment should use per-user credential vaulting, authorization, quotas and licensing checks rather than a shared server token.

After authenticating to FinReview, GET `/v2/market/providers` reports *configuration presence*, not credential validity. GET `/v2/market/history?provider=upstox&instrument=NSE_EQ%7C<instrument_key>&start=2025-01-01&end=2025-01-31` returns normalized daily OHLCV when explicitly enabled. FYERS requires its own `NSE:<SYMBOL>-EQ` instrument format. Each request is limited to 365 calendar days; this is an application safety limit and **not** a provider historical coverage statement.

The `/v2/market/csv/validate` endpoint accepts a raw UTF-8 CSV request body for an authenticated user, max 3 MB / 50,000 rows. It validates but **does not persist** data.

Example CSV:

```csv
timestamp,open,high,low,close,volume
2025-01-02T15:30:00+05:30,100,105,98,104,500
2025-01-03T15:30:00+05:30,104,106,101,102,550
```

## Data provenance and limitations

- All timestamps are normalized to UTC, but date filtering uses Asia/Kolkata exchange-local dates.
- All prices and volumes must be finite. OHLC must be internally consistent; volume nonnegative. Duplicate timestamps are rejected.
- The response reports `adjustment=provider_raw_unverified` rather than pretending bars are split/dividend adjusted.
- This stage has a database candle-cache foundation, but no automatic ingestion/synchronization worker or consumer-facing import endpoint. It does not yet infer official market holidays, audit corporate actions or support a validated backtest.
- Cached daily candles are isolated per user, provider and instrument; repeated identical imports are idempotent. Changed provider candles are rejected pending explicit reconciliation.
- The authenticated `/v2/market/analyze-cached` route produces SMA and breakout research observations from cached **completed** daily bars only. This returns NO_DATA/INSUFFICIENT_DATA when appropriate, but does not create persisted trade signals.
- CSV imports are a **validation-only** path until dedicated, per-user storage, limits, provenance and rollback are built.
- Exchange-data rights differ from account/API access. Verify storage, analysis, charting, backtesting and redistribution rights with your provider before production use.
- Historical API availability and rate limits can change. No uninterrupted data coverage is guaranteed.
- Broker auth tokens may expire; manage session renewal using provider-approved flows only.
- The FYERS helper has no order placement method. FinReview v2 remains research-only.

## Required before v2.0.0

Complete a real read-only provider integration check with permissioned credentials, add per-user throttling/caching if opening historical API to multiple users, build strategy signals and calendar-aware reporting, verify paper-trading reconciliation and security, and stage-test the complete interface.
