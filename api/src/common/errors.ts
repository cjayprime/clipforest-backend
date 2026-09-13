/**
 * Error taxonomy (PRD §19.3): VIDEO_*, UPLOAD_*, TRANSCRIPTION_*, ANALYSIS_*,
 * RENDER_*, STORAGE_*, SYSTEM_*, plus API-level AUTH_* / VALIDATION_* codes, with
 * the messages users see.
 */
import { QueryFailedError } from 'typeorm';

/** Every error the API renders in the envelope `{ error: { code, message, retryable, correlationId } }`. */
export class AppError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status = 400,
    public readonly retryable = false,
    public readonly details?: unknown,
  ) {
    super(message);
  }
}

export const Errors = {
  notFound: (what = 'Resource') => new AppError('NOT_FOUND', `${what} not found.`, 404),
  unauthorized: () => new AppError('AUTH_REQUIRED', 'Sign in to continue.', 401),
  invalidCredentials: () => new AppError('AUTH_INVALID_CREDENTIALS', 'Email or password is incorrect.', 401),
  emailTaken: () => new AppError('AUTH_EMAIL_TAKEN', 'An account with this email already exists.', 409),
  invalidResetToken: () =>
    new AppError(
      'AUTH_RESET_TOKEN_INVALID',
      'This password reset link is invalid or has expired. Request a new one.',
      400,
    ),
  wrongPassword: () => new AppError('AUTH_PASSWORD_INCORRECT', 'Your current password is incorrect.', 400),
  passwordUnchanged: () =>
    new AppError('AUTH_PASSWORD_UNCHANGED', 'Your new password must be different from your current one.', 400),
  mailUnavailable: () =>
    new AppError('MAIL_UNAVAILABLE', 'We could not send the email just now. Please try again shortly.', 503, true),
  billingNotConfigured: () =>
    new AppError('BILLING_NOT_CONFIGURED', 'Subscriptions are not available yet.', 503),
  billingUnavailable: () =>
    new AppError('BILLING_UNAVAILABLE', 'Could not reach the payment provider. Please try again shortly.', 503, true),
  planUnavailable: (planName: string, interval: 'month' | 'year') =>
    new AppError(
      'BILLING_PLAN_UNAVAILABLE',
      `${planName} is not available with ${interval === 'year' ? 'annual' : 'monthly'} billing yet.`,
      400,
    ),
  noSubscription: () => new AppError('BILLING_NO_SUBSCRIPTION', 'You do not have an active subscription.', 409),
  insufficientCredits: (needed: number, available: number) =>
    new AppError(
      'BILLING_INSUFFICIENT_CREDITS',
      `This needs ${needed} credits and you have ${available}. Top up or wait for your next renewal.`,
      402,
    ),
  invalidWebhookSignature: () => new AppError('WEBHOOK_SIGNATURE_INVALID', 'Invalid webhook signature.', 401),
  rightsRequired: () =>
    new AppError(
      'VIDEO_RIGHTS_CONFIRMATION_REQUIRED',
      'Confirm that you own or are authorized to process this content.',
      400,
    ),
  unsupportedSource: (reason: string) => new AppError('VIDEO_SOURCE_UNSUPPORTED', reason, 400),
  unsupportedFile: (reason: string) => new AppError('VIDEO_UNSUPPORTED_FILE', reason, 400),
  uploadTooLarge: (max: number) =>
    new AppError('UPLOAD_TOO_LARGE', `File exceeds the maximum upload size of ${Math.round(max / 1024 ** 3)} GB.`, 413),
  uploadObjectMissing: () =>
    new AppError('UPLOAD_OBJECT_MISSING', 'The uploaded file could not be found in storage. Please retry the upload.', 409, true),
  invalidState: (message: string) => new AppError('INVALID_STATE', message, 409),
  notRetryable: (message: string) => new AppError('NOT_RETRYABLE', message, 409),
  invalidRange: (message: string) => new AppError('RENDER_INVALID_RANGE', message, 400),
  storage: (message: string) => new AppError('STORAGE_UNAVAILABLE', message, 503, true),
};

/**
 * True when a write lost a race against an existing row (Postgres 23505).
 *
 * A credit grant treats this as "already applied" and a render as "someone else
 * created it", so it must never throw: `driverError` is untyped and can be
 * absent, which is why the shape is checked rather than asserted.
 */
export function isUniqueViolation(err: unknown): boolean {
  if (!(err instanceof QueryFailedError)) return false;
  const driverError: unknown = err.driverError;
  if (typeof driverError !== 'object' || driverError === null) return false;
  return (driverError as { code?: unknown }).code === '23505';
}
