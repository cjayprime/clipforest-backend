/**
 * Standard Webhooks signature verification for Polar deliveries.
 *
 * Implemented directly rather than pulled from a library because Polar has two
 * secret encodings, split by when the secret was created:
 *
 *   on/after 8 Sep 2026  the `whsec_…` secret is standard: base64-decode the
 *                        part after the prefix to get the HMAC key
 *   before 8 Sep 2026    the HMAC key is the UTF-8 bytes of the *whole*
 *                        `whsec_…` string, prefix included
 *
 * Polar's own SDKs try both, and so does this: a deployment must keep working
 * whichever vintage of secret it was given.
 *
 * Signed content is `${webhook-id}.${webhook-timestamp}.${raw body}`, HMAC-SHA256,
 * base64. The `webhook-signature` header is a space-separated list of
 * `v1,<signature>` entries, so key rotation can present several at once.
 */
import { createHmac, timingSafeEqual } from 'node:crypto';

export interface WebhookSignatureHeaders {
  /** `webhook-id` */
  id: string | undefined;
  /** `webhook-timestamp`, seconds since the epoch */
  timestamp: string | undefined;
  /** `webhook-signature` */
  signature: string | undefined;
}

export interface VerifyWebhookInput {
  secret: string;
  headers: WebhookSignatureHeaders;
  /** The exact bytes received, never a re-serialized object. */
  body: string | Buffer;
  /** Replay window in seconds. */
  toleranceSec?: number;
  now?: Date;
}

/** Both key encodings Polar may have handed out, newest convention first. */
export function webhookSigningKeys(secret: string): Buffer[] {
  const keys: Buffer[] = [];
  if (secret.startsWith('whsec_')) {
    const base64 = secret.slice('whsec_'.length);
    try {
      const decoded = Buffer.from(base64, 'base64');
      if (decoded.length > 0) keys.push(decoded);
    } catch {
      /* not base64: only the literal encoding applies */
    }
  }
  // Legacy Polar HMAC: the key is the UTF-8 bytes of the full secret string.
  keys.push(Buffer.from(secret, 'utf8'));
  return keys;
}

function equal(a: string, b: string): boolean {
  const left = Buffer.from(a, 'utf8');
  const right = Buffer.from(b, 'utf8');
  // timingSafeEqual throws on a length mismatch, which is itself not secret.
  if (left.length !== right.length) return false;
  return timingSafeEqual(left, right);
}

/** The `v1,<sig>` entries from a `webhook-signature` header. */
function providedSignatures(header: string): string[] {
  return header
    .split(' ')
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const comma = part.indexOf(',');
      return comma === -1 ? part : part.slice(comma + 1);
    })
    .filter(Boolean);
}

export function verifyWebhookSignature(input: VerifyWebhookInput): boolean {
  const { secret, headers, body } = input;
  const toleranceSec = input.toleranceSec ?? 300;
  const now = input.now ?? new Date();

  if (!secret || !headers.id || !headers.timestamp || !headers.signature) return false;

  const sentAt = Number(headers.timestamp);
  if (!Number.isFinite(sentAt)) return false;
  // Reject replays, and clocks far enough ahead to be suspicious.
  const driftSec = Math.abs(now.getTime() / 1000 - sentAt);
  if (driftSec > toleranceSec) return false;

  const payload = `${headers.id}.${headers.timestamp}.${typeof body === 'string' ? body : body.toString('utf8')}`;
  const provided = providedSignatures(headers.signature);
  if (provided.length === 0) return false;

  for (const key of webhookSigningKeys(secret)) {
    const expected = createHmac('sha256', key).update(payload, 'utf8').digest('base64');
    for (const candidate of provided) {
      if (equal(expected, candidate)) return true;
    }
  }
  return false;
}
