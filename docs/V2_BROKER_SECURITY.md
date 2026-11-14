# FinReview v2.0.1 — Broker credential security foundation

**Development-only, NOT a production-ready brokerage login flow.** This document covers the encrypted-at-rest storage foundation on release/2.0.1. FYERS, Upstox and Zerodha remain optional; users may choose different providers for historical candles and live quotes.

## Implemented

- Per-user, per-broker, per-purpose connection records in a separate additive table, encrypted at rest with Fernet.
- Account/purpose binding within the authenticated ciphertext; wrong-account or wrong-purpose access is rejected.
- No browser or public REST endpoint to write raw access tokens or API secrets. An opt-in (`FINREVIEW_V2_OAUTH_ENABLED=true`) server-side broker authorization-code flow now exists, but is disabled by default until independently validated. It accepts only broker-returned one-time codes and opaque state.
- Authenticated `GET /v2/market/connections`: metadata only; never returns credentials or encrypted ciphertext.
- Authenticated `DELETE /v2/market/connections/{provider}`: removes the authenticated user's locally stored credentials for that broker, for both quote and historical purposes. This **does not revoke** tokens at the broker.
- Expired sessions are not decrypted for use; wrong/missing vault key fails closed.
- Automated regression tests for encryption, isolation, expiration, local disconnect and incorrect keys.

## Vault key management

The trusted backend uses `FINREVIEW_BROKER_VAULT_KEY` (Fernet 32-byte random symmetric key, URL-safe Base64 encoded) from a server-side secret manager. Do not put it into Git, the browser, a public environment variable, an HTTP URL, logs or screenshots. Generate a key on your private machine:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Use an independent key per environment. Without the key the vault **cannot decrypt** stored credentials; losing it makes encrypted connections unrecoverable. Key rotation and secure multi-key migration are still outstanding. Use staging only until audited.

**Do not configure or enable this as a public account-connection feature yet.** The trusted `store_trusted` helper is an internal primitive intended for a future broker-approved server-to-server OAuth callback; it is not wired to public requests. The existing dashboard lets users save only nonsecret provider preferences.

## Before enabling broker connectivity

1. Implement each broker's approved login/callback exchange with anti-CSRF state, PKCE where supported, allowlisted redirect URIs and verified session ownership.
2. Make app-client configuration, user consent and broker entitlements explicit; never assume an historical Analytics Token authorizes live data.
3. Add controlled session refresh/expiry handling, per-user API rate limits, audit records, encrypted key rotation and upstream token revocation where supported.
4. Perform staged live read-only smoke tests and security review with real permissioned accounts.
5. Replace deployment-wide broker tokens for public multi-user endpoints with per-user connections. Current historical-data API is default-disabled for this reason.

The current storage foundation does not prove live broker connectivity, compliance with exchange redistribution licenses, or authorization to place orders. Real trading remains out of scope.


## Authorization-code flow in v2.0.1 (disabled by default)

The server supports provider-specific login URL construction, a randomly generated single-use state (SHA-256 hash stored in the database, 5-minute expiry), exact broker-bound state claiming, and a server-to-server auth-code exchange. The callback is purposely **not** enabled in any existing deployment. No real-broker OAuth handshake has been exercised.

| Broker | Registered HTTPS callback | Broker-provided callback fields |
| --- | --- | --- |
| FYERS | `/v2/market/oauth/fyers/callback` | `auth_code`, `state` |
| Upstox | `/v2/market/oauth/upstox/callback` | `code`, `state` |
| Zerodha | `/v2/market/oauth/zerodha/callback` | `request_token`, `finreview_state` (passed through Kite `redirect_params`; verify with actual app) |

Development-only configuration variables:

```dotenv
FINREVIEW_V2_OAUTH_ENABLED=false
FINREVIEW_BROKER_VAULT_KEY=
FYERS_CLIENT_ID=
FYERS_APP_SECRET=
FINREVIEW_FYERS_REDIRECT_URI=
UPSTOX_CLIENT_ID=
UPSTOX_CLIENT_SECRET=
FINREVIEW_UPSTOX_REDIRECT_URI=
ZERODHA_API_KEY=
ZERODHA_API_SECRET=
FINREVIEW_ZERODHA_REDIRECT_URI=
```

All three redirect URIs must be **HTTPS** and exactly correspond to their broker's registered redirect URI. The service does not accept arbitrary redirect URIs from browser requests. `POST /v2/market/oauth/{provider}/start` requires a FinReview bearer login and, when explicitly enabled, returns a broker-hosted authorization URL. `GET /v2/market/oauth/{provider}/callback` checks the original one-time state before attempting a broker token exchange. On success, only `STORED_UNVERIFIED` metadata is returned; the access token never appears in the API response.

**Critical next gates:** perform a live broker callback smoke test in a private staging environment, ensure load balancer/access logs redact query parameters (authorization codes), validate each broker's callback state echo (especially Zerodha redirect_params), account identity and data entitlements, derive and enforce provider-specific token expiration, implement upstream revocation and token rotation, and add abuse/rate limits and audit monitoring. Do not flip `FINREVIEW_V2_OAUTH_ENABLED` to true on the public Render deployment until these checks are complete.

Documentation used: [FYERS individual-app auth](https://fyers.in/api-docs/v3/api/authentication/individual-app/generate-auth-code/), [FYERS token exchange](https://fyers.in/api-docs/v3/api/authentication/individual-app/validate-auth-code/), [Upstox auth](https://upstox.com/developer/api-documentation/authentication/), [Kite login](https://kite.trade/docs/connect/v3/user/).
