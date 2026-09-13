/**
 * TypeORM entities (PRD §13).
 *
 * Table and column names are snake_case and pinned explicitly: the Python media
 * worker reads and writes these same tables with plain SQL, so the physical
 * schema is a shared contract. Status columns are TEXT guarded by CHECK
 * constraints (see src/migrations) so both runtimes share one vocabulary. All
 * timings are integer milliseconds; all timestamps are UTC (timestamptz).
 *
 * Relations use lazy `() => Entity` callbacks, which is what keeps the circular
 * imports between Video, User, Transcript, Candidate and Render safe.
 */
import { Candidate } from './candidate.entity';
import { CreditBalance } from './credit-balance.entity';
import { CreditLedgerEntry } from './credit-ledger-entry.entity';
import { JobRun } from './job-run.entity';
import { PasswordResetToken } from './password-reset-token.entity';
import { ProcessedWebhook } from './processed-webhook.entity';
import { Render } from './render.entity';
import { Subscription } from './subscription.entity';
import { Transcript } from './transcript.entity';
import { UsageEvent } from './usage-event.entity';
import { User } from './user.entity';
import { Video } from './video.entity';

export {
  Candidate,
  CreditBalance,
  CreditLedgerEntry,
  JobRun,
  PasswordResetToken,
  ProcessedWebhook,
  Render,
  Subscription,
  Transcript,
  UsageEvent,
  User,
  Video,
};
export type { ComponentScores } from './candidate.entity';
export type { RenderSettingsJson } from './render.entity';
export type { TranscriptSegmentJson, TranscriptWordJson } from './transcript.entity';
export { bigintTransformer, numericTransformer } from './transformers';

/**
 * Every entity the DataSource registers, and what CoreModule stubs in
 * OPENAPI_ONLY mode. A new entity file is invisible until it is listed here.
 */
export const ENTITIES = [
  User,
  PasswordResetToken,
  Video,
  Transcript,
  Candidate,
  Render,
  JobRun,
  UsageEvent,
  Subscription,
  CreditBalance,
  CreditLedgerEntry,
  ProcessedWebhook,
];
