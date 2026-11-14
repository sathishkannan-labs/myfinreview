# FinReview v2.0.1 — Broker credential security foundation

**Development-only, NOT a complete brokerage login flow.** This document covers the encrypted-at-rest storage foundation on release/2.0.1. FYERS, Upstox and Zerodha remain optional; users may choose different providers for historical candles and live quotes.

## Implemented

- Per-user, per-broker, per-purpose connection records in a separate additive table, encrypted at rest with Fernet.
- Account/purpose binding within the authenticated ciphertext; wrong-account or wrong-purpose access is rejected.
- No browser or public REST endpoint to write raw access tokens or API secrets.
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
