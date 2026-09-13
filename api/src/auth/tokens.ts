/** Password reset tokens and session revocation rules. */
import { createHash, randomBytes } from 'node:crypto';

/**
 * The reset token: 32 random bytes, base64url encoded. Only its SHA-256 is ever
 * persisted, so the plaintext exists in the email and nowhere else and reading
 * the table cannot reset an account.
 */
export const RESET_TOKEN_BYTES = 32;

/** Base64url of 32 bytes — never contains characters that need URL escaping. */
export const RESET_TOKEN_PATTERN = /^[A-Za-z0-9_-]{43}$/;

export function hashResetToken(token: string): string {
  return createHash('sha256').update(token, 'utf8').digest('hex');
}

export function generateResetToken(): { token: string; tokenHash: string } {
  const token = randomBytes(RESET_TOKEN_BYTES).toString('base64url');
  return { token, tokenHash: hashResetToken(token) };
}

/** When a reset link stops working. */
export function resetTokenExpiry(ttlMinutes: number, now: Date = new Date()): Date {
  return new Date(now.getTime() + ttlMinutes * 60_000);
}

/** A token is redeemable once, before it expires. */
export function isResetTokenUsable(
  row: { expiresAt: Date; usedAt: Date | null } | null | undefined,
  now: Date = new Date(),
): boolean {
  if (!row) return false;
  if (row.usedAt !== null) return false;
  return row.expiresAt.getTime() > now.getTime();
}

/**
 * Absolute link to the web app's reset page. Built from the configured public
 * URL rather than any request header, so a spoofed Host cannot redirect the
 * token to an attacker's origin.
 */
export function buildResetUrl(publicWebUrl: string, token: string): string {
  const url = new URL('/reset-password', publicWebUrl);
  url.searchParams.set('token', token);
  return url.toString();
}

/**
 * A session is revoked when the password changed after it was issued, compared
 * in milliseconds. The session issued together with a password change is signed
 * after the change is recorded, so it stays valid.
 */
export function isSessionRevoked(issuedAtMs: number, passwordChangedAt: Date | null | undefined): boolean {
  if (!passwordChangedAt) return false;
  return passwordChangedAt.getTime() > issuedAtMs;
}
