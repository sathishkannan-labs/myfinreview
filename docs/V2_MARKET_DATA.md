# FinReview 2.x — bring your own historical market data (BYO API)

This guide covers optional broker adapters in FinReview Community Edition v2.0.1. The public demo uses CSV and synthetic sample research when updated to this release. Those workflows require no broker account.

## Provider status

| Provider | Data type | Implementation status | Key requirement |
| --- | --- | --- | --- |
| Upstox V3 | Historical daily OHLCV and LTP adapter | Backend only, mock-tested; live unverified | Analytics Token for history and a quote-authorized token in the LTP configuration slot |
| FYERS V3 Standard | Historical daily OHLCV + quote adapter | Verified through the allowlisted owner workflow on staging | User-supplied authorized session |
| Zerodha Kite Connect V3 | Historical daily OHLCV + LTP quotes | Implemented; mocked CI tested, live unverified | User-supplied **paid, data-enabled** Kite Connect session (Personal/free API excludes market data) |
| CSV | UTC/offset-stamped OHLCV validation | Implemented, mocked CI tested | User-owned, licensed dataset |
| Yahoo Finance | Existing v1 portfolio pricing (separate code) | Existing, not upgraded to v2 provider contract | Verify Yahoo usage restrictions |

Upstox docs: https://upstox.com/developer/api-documentation/
FYERS docs: https://fyers.in/api-docs/
Zerodha historical docs: https://kite.trade/docs/connect/v3/historical/
Zerodha quotes docs: https://kite.trade/docs/connect/v3/market-quotes/
Zerodha pricing: https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/what-are-the-charges-for-kite-apis

## Research Lab availability

Community users can analyze their own historical OHLCV CSV or run the synthetic sample. The maintainer's staging deployment also exposes FYERS personal research to its allowlisted owner. Its OAuth success redirect is fixed to the maintainer's staging frontend.

Upstox and Zerodha are backend adapters only in this release. The Lab has no provider selector for them or browser OAuth flow. The server rejects Upstox and Zerodha OAuth starts and callbacks. The account-scoped `GET/PUT /v2/market/preference` endpoints store non-secret preferences only.

For setup, see [the environment template](../.env.example). For Docker Compose, copy it to `.env` in the repository root. For direct backend startup, copy it to `backend/.env`. Leave the broker flags off for Community CSV research and public demo deployments.

## Private deployment configuration

Do not put credentials in Git, screenshots, README examples, Telegram, frontend JS or public deployments.

```dotenv
FINREVIEW_V2_MARKET_API_ENABLED=false
UPSTOX_ANALYTICS_TOKEN=
FYERS_CLIENT_ID=
FYERS_ACCESS_TOKEN=
UPSTOX_ACCESS_TOKEN=  # market-data-authorized token for LTP; different from historical Analytics Token
ZERODHA_API_KEY=
ZERODHA_ACCESS_TOKEN=
```

The new `/v2/market/history` route is **off by default** because a public web API that uses one server-side token could become an unauthorized third-party exchange-data proxy. Enable only when the deployment is private, authenticated, usage-limited, and permitted by provider terms. A public multi-user deployment should use per-user credential vaulting, authorization, quotas and licensing checks rather than a shared server token.

After authenticating to FinReview, GET `/v2/market/providers` reports *configuration presence*, not credential validity. GET `/v2/market/history?provider=upstox&instrument=NSE_EQ%7C<instrument_key>&start=2025-01-01&end=2025-01-31` returns normalized daily OHLCV when explicitly enabled. FYERS requires its own `NSE:<SYMBOL>-EQ` instrument format. Zerodha's **historical** API requires the numeric `instrument_token` from its instruments master (example `408065`); Zerodha's **LTP quotes** API accepts `NSE:INFY`-style exchange:tradingsymbol identifiers. Do not assume provider symbols are interchangeable. Each request is limited to 365 calendar days; this is an application safety limit and **not** a provider historical coverage statement.

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
- This stage has a database candle-cache foundation and an operator-invoked opt-in synchronization function (`intelligence.pipeline.synchronize_and_research`), but no automatic scheduler, operator CLI or consumer-facing import endpoint. It does not yet infer official market holidays, audit corporate actions or support a validated backtest.
- Cached daily candles are isolated per user, provider and instrument; repeated identical imports are idempotent. Changed provider candles are rejected pending explicit reconciliation.
- The authenticated `/v2/market/analyze-cached` route produces SMA and breakout research observations from cached **completed** daily bars only. This returns NO_DATA/INSUFFICIENT_DATA when appropriate, but does not create persisted trade signals.
- CSV analysis is read-only. The research endpoint validates the file, computes a report, and does not persist the input candles.
- Exchange-data rights differ from account/API access. Verify storage, analysis, charting, backtesting and redistribution rights with your provider before production use.
- Historical API availability and rate limits can change. No uninterrupted data coverage is guaranteed.
- Broker auth tokens may expire; manage session renewal using provider-approved flows only.
- FYERS, Upstox and Zerodha are optional, with no mandatory or default broker. Account owners can independently save non-secret historical/quotes broker preferences via `GET` and `PUT /v2/market/preference`; this does not connect credentials.
- Broker environment variables are **deployment-level** in the current foundation, not per-user. A public multi-user hosted edition requires encrypted per-user credential vaulting, OAuth/session renewal, entitlements, throttling and explicit consent before live broker calls are enabled.
- The FYERS, Upstox and Zerodha adapters contain no order placement methods. FinReview v2 remains research-only.


## Version 2.0.1 LTP quote groundwork

The internal `intelligence.live_quotes.load_quotes` interface now normalizes **read-only LTP snapshots** across FYERS, Upstox V3 and Zerodha. Provider-specific identifiers remain distinct. Each normalized result contains provider, instrument, last price and an explicit `timestamp_verified=false` value when the quote endpoint supplies no independently validated trade timestamp. Do not treat a snapshot receipt time as the exchange trade time, and do not use these snapshots for automatic paper fills until stronger source-time and exchange-calendar checks are implemented.

FinReview reads `UPSTOX_ACCESS_TOKEN` for LTP and `UPSTOX_ANALYTICS_TOKEN` for history. These names are application configuration slots. Verify the token is allowed to request your intended endpoint under the [Upstox Analytics Token documentation](https://upstox.com/developer/api-documentation/analytics-token/). No public API route for server-side shared tokens is exposed here. Upstox and Zerodha quote adapters have mocked coverage. FYERS quote snapshots were verified for the staging owner.

Official docs: [Upstox V3 LTP](https://upstox.com/developer/api-documentation/ltp-v3/), [FYERS quotes](https://fyers.in/api-docs/v3/api/market-data/quotes/), [Zerodha market quotes](https://kite.trade/docs/connect/v3/market-data-and-instruments/).

For v2.0.1 development, the encrypted broker connection storage foundation is documented in [V2_BROKER_SECURITY.md](V2_BROKER_SECURITY.md). FYERS OAuth is enabled only for the configured staging owner. The connection status and disconnect routes are scoped to the authenticated account.

## Remaining production-readiness gates

Complete a real read-only provider integration check with permissioned credentials, add per-user throttling/caching if opening historical API to multiple users, build strategy signals and calendar-aware reporting, verify paper-trading reconciliation and security, and stage-test the complete interface.
