# Accounts, passwords and email

## Endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/auth/register` | public | Create an account; signs in and sends a welcome email |
| `POST` | `/api/auth/login` | public | Sign in |
| `POST` | `/api/auth/logout` | public | Clear the session cookie |
| `POST` | `/api/auth/forgot-password` | public | Email a reset link. Always `202` |
| `POST` | `/api/auth/reset-password` | public | Redeem a link, set a new password, sign in |
| `POST` | `/api/auth/change-password` | session | Change the password using the current one |
| `GET` | `/api/auth/me` | session | Profile and usage |

Sessions are a JWT in the `httpOnly`, `SameSite=Lax` cookie `cr_session`. The browser never sees a token in JavaScript.

## Password reset

1. `forgot-password` looks the address up. **The response is `202` either way** — a different answer for unknown addresses would turn the endpoint into a way to discover who has an account. For the same reason a delivery failure is logged, not returned: a `503` only for real accounts would leak just as much.
2. A 32-byte token is generated, base64url encoded. Only its **SHA-256 is stored**; the plaintext exists in the email and nowhere else, so reading `password_reset_tokens` does not let anyone reset an account.
3. Requesting a new link marks every earlier unused link for that account as used, so only the newest works.
4. Redeeming is **single use** and expires after `PASSWORD_RESET_TTL_MINUTES` (default 60). A used or expired token returns `AUTH_RESET_TOKEN_INVALID` — the same error either way.
5. The link is built from `PUBLIC_WEB_URL`, never from a request header, so a spoofed `Host` cannot point the token at another origin.

`change-password` additionally requires the current password, and rejects a new password equal to the old one.

## Signing out other devices

`users.password_changed_at` is set whenever the password changes. A session is refused when the JWT's `iat` is older than that instant, so a reset or change signs out every other device.

By default (`REVOCATION_CACHE_SECONDS=0`) this is checked on **every** request, so other devices stop working immediately. The cost is one indexed primary-key lookup on requests that already query the database. Raising the value caches `password_changed_at` in memory for that many seconds, trading immediacy for fewer reads — a session revoked elsewhere then survives for up to that long.

The device that performed the change is handed a fresh cookie and stays signed in.

Because `iat` has whole-second precision while the timestamp has milliseconds, the comparison truncates to seconds — otherwise the cookie issued microseconds after the change would invalidate itself.

The check fails **open**: if the lookup errors the request is allowed and a warning is logged, so a database blip does not sign out every user at once.

## Email

`MailService` (`src/mail/`) is general-purpose — inject it anywhere, not just in auth. Templates live in `src/mail/templates.ts` and return subject, HTML and plain text; the service picks a provider and sends.

| `MAIL_PROVIDER` | Behaviour |
| --- | --- |
| `auto` (default) | Brevo when `BREVO_API_KEY` is set, otherwise `console` |
| `brevo` | `POST https://api.brevo.com/v3/smtp/email` with the `api-key` header |
| `console` | Prints the rendered message to the API log, **including reset links** |
| `noop` | Accepts and discards |

Three messages are sent today: **welcome** on registration, **password reset**, and **password changed**. All three go out through `sendQuietly`, which logs and swallows failures — signing up or changing a password is already committed, so a mail outage must not fail the request or roll it back.

### Brevo setup

1. Create an API key (Brevo → SMTP & API → API keys) and set `BREVO_API_KEY`.
2. Set `MAIL_FROM_EMAIL` to a sender Brevo has **verified** for your account — either a verified domain or a registered single sender. Brevo rejects anything else, and the default `no-reply@cliprover.local` will not work.
3. Optionally set `MAIL_REPLY_TO` and `MAIL_FROM_NAME`.

Failures are logged with the Brevo error code and never with the recipient or the message body.

### Reading email in development

With no API key, emails are printed to the API log — so the reset link is in your terminal.

`MAIL_TEST_INBOX=1` additionally keeps the last 20 messages in memory and serves them from `GET /api/dev/mail`, which is how the browser tests follow a reset link. It is **forced off whenever `NODE_ENV=production`**, regardless of the variable, and is excluded from the OpenAPI document.

## Rate limits and errors

Every auth route is limited to `RATE_LIMIT_AUTH_PER_MIN` (default 20) per IP, or per user once signed in.

| Code | Status | Meaning |
| --- | --- | --- |
| `AUTH_INVALID_CREDENTIALS` | 401 | Wrong email or password (identical for both) |
| `AUTH_EMAIL_TAKEN` | 409 | Address already registered |
| `AUTH_RESET_TOKEN_INVALID` | 400 | Reset link unknown, used or expired |
| `AUTH_PASSWORD_INCORRECT` | 400 | Current password wrong on change |
| `AUTH_PASSWORD_UNCHANGED` | 400 | New password equals the old one |
| `AUTH_REQUIRED` | 401 | No session, or one revoked by a password change |

Sign-in compares against a dummy hash when the account does not exist, so response timing does not reveal registered addresses.
