/**
 * Error taxonomy (PRD §19.3): VIDEO_*, UPLOAD_*, TRANSCRIPTION_*, ANALYSIS_*,
 * RENDER_*, STORAGE_*, SYSTEM_*, plus API-level AUTH_* / VALIDATION_* codes.
 * Every error response has the shape in PRD §12.3.
 */
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
