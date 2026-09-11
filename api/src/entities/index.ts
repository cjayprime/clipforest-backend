/**
 * TypeORM entities (PRD §13).
 *
 * Table and column names are snake_case and pinned explicitly: the Python media
 * worker reads and writes these same tables with plain SQL, so the physical
 * schema is a shared contract. Status columns are TEXT guarded by CHECK
 * constraints (see src/migrations) so both runtimes share one vocabulary.
 * All timings are integer milliseconds; all timestamps are UTC (timestamptz).
 */
import {
  Column,
  CreateDateColumn,
  Entity,
  Index,
  JoinColumn,
  ManyToOne,
  OneToMany,
  OneToOne,
  PrimaryGeneratedColumn,
  Unique,
  UpdateDateColumn,
} from 'typeorm';

/** Postgres bigint arrives as a string; expose it as a number (values stay far below 2^53). */
const bigintTransformer = {
  to: (v: number | null | undefined) => (v === null || v === undefined ? v : String(v)),
  from: (v: string | null): number | null => (v === null || v === undefined ? null : Number(v)),
};

const numericTransformer = {
  to: (v: number | null | undefined) => v,
  from: (v: string | number | null): number | null => (v === null || v === undefined ? null : Number(v)),
};

@Entity('users')
export class User {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ unique: true })
  email: string;

  @Column({ name: 'password_hash' })
  passwordHash: string;

  @Column({ name: 'display_name', type: 'text', nullable: true })
  displayName: string | null;

  @Column({ default: 'free' })
  plan: string;

  @Column({ type: 'jsonb', nullable: true })
  limits: Record<string, unknown> | null;

  @CreateDateColumn({ name: 'created_at', type: 'timestamptz' })
  createdAt: Date;

  @UpdateDateColumn({ name: 'updated_at', type: 'timestamptz' })
  updatedAt: Date;

  @OneToMany(() => Video, (v) => v.user)
  videos: Video[];
}

@Index('videos_user_id_created_at_idx', ['userId', 'createdAt'])
@Index('videos_status_idx', ['status'])
@Entity('videos')
export class Video {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ name: 'user_id', type: 'uuid' })
  userId: string;

  @Column({ name: 'source_type' })
  sourceType: string;

  @Column({ name: 'source_url', type: 'text', nullable: true })
  sourceUrl: string | null;

  @Column({ name: 'source_provider', type: 'text', nullable: true })
  sourceProvider: string | null;

  @Column({ name: 'object_key', type: 'text', nullable: true })
  objectKey: string | null;

  @Column({ name: 'original_filename' })
  originalFilename: string;

  @Column()
  title: string;

  @Column({ name: 'content_type', type: 'text', nullable: true })
  contentType: string | null;

  @Column({ name: 'size_bytes', type: 'bigint', nullable: true, transformer: bigintTransformer })
  sizeBytes: number | null;

  @Column({ name: 'duration_ms', type: 'int', nullable: true })
  durationMs: number | null;

  @Column({ type: 'int', nullable: true })
  width: number | null;

  @Column({ type: 'int', nullable: true })
  height: number | null;

  @Column({ type: 'double precision', nullable: true, transformer: numericTransformer })
  fps: number | null;

  @Column({ type: 'text', nullable: true })
  orientation: string | null;

  @Column({ name: 'has_audio', type: 'boolean', nullable: true })
  hasAudio: boolean | null;

  @Column({ name: 'video_codec', type: 'text', nullable: true })
  videoCodec: string | null;

  @Column({ name: 'audio_codec', type: 'text', nullable: true })
  audioCodec: string | null;

  @Column({ type: 'text', nullable: true })
  container: string | null;

  @Column({ type: 'text', nullable: true })
  language: string | null;

  @Column({ default: 'CREATED' })
  status: string;

  @Column({ type: 'int', default: 0 })
  progress: number;

  @Column({ type: 'text', nullable: true })
  stage: string | null;

  @Column({ type: 'text', nullable: true })
  substage: string | null;

  @Column({ name: 'pipeline_version' })
  pipelineVersion: string;

  @Column({ name: 'processing_run', type: 'int', default: 0 })
  processingRun: number;

  @Column({ name: 'analysis_run', type: 'int', default: 0 })
  analysisRun: number;

  @Column({ name: 'error_code', type: 'text', nullable: true })
  errorCode: string | null;

  @Column({ name: 'error_message', type: 'text', nullable: true })
  errorMessage: string | null;

  @Column({ name: 'error_retryable', type: 'boolean', nullable: true })
  errorRetryable: boolean | null;

  @Column({ name: 'error_correlation_id', type: 'text', nullable: true })
  errorCorrelationId: string | null;

  @Column({ name: 'failed_stage', type: 'text', nullable: true })
  failedStage: string | null;

  @Column({ name: 'thumbnail_key', type: 'text', nullable: true })
  thumbnailKey: string | null;

  @Column({ name: 'proxy_key', type: 'text', nullable: true })
  proxyKey: string | null;

  @Column({ name: 'audio_key', type: 'text', nullable: true })
  audioKey: string | null;

  @Column({ name: 'upload_id', type: 'text', nullable: true })
  uploadId: string | null;

  @Column({ name: 'upload_completed_at', type: 'timestamptz', nullable: true })
  uploadCompletedAt: Date | null;

  @Column({ name: 'rights_confirmed_at', type: 'timestamptz' })
  rightsConfirmedAt: Date;

  @Column({ name: 'active_transcript_id', type: 'uuid', nullable: true, unique: true })
  activeTranscriptId: string | null;

  @Column({ name: 'ready_at', type: 'timestamptz', nullable: true })
  readyAt: Date | null;

  @Column({ name: 'source_expired_at', type: 'timestamptz', nullable: true })
  sourceExpiredAt: Date | null;

  @Column({ name: 'deleted_at', type: 'timestamptz', nullable: true })
  deletedAt: Date | null;

  @Column({ name: 'purged_at', type: 'timestamptz', nullable: true })
  purgedAt: Date | null;

  @CreateDateColumn({ name: 'created_at', type: 'timestamptz' })
  createdAt: Date;

  @UpdateDateColumn({ name: 'updated_at', type: 'timestamptz' })
  updatedAt: Date;

  @ManyToOne(() => User, (u) => u.videos, { onDelete: 'CASCADE' })
  @JoinColumn({ name: 'user_id' })
  user: User;

  @OneToOne(() => Transcript, { onDelete: 'SET NULL' })
  @JoinColumn({ name: 'active_transcript_id' })
  activeTranscript: Transcript | null;

  @OneToMany(() => Candidate, (c) => c.video)
  candidates: Candidate[];

  @OneToMany(() => Render, (r) => r.video)
  renders: Render[];
}

export interface TranscriptWordJson {
  startMs: number;
  endMs: number;
  text: string;
  confidence?: number;
  speaker?: string | null;
}

export interface TranscriptSegmentJson extends TranscriptWordJson {
  id: string;
}

@Unique('transcripts_video_id_version_key', ['videoId', 'version'])
@Entity('transcripts')
export class Transcript {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ name: 'video_id', type: 'uuid' })
  videoId: string;

  @Column({ type: 'int' })
  version: number;

  @Column()
  provider: string;

  @Column({ type: 'text', nullable: true })
  language: string | null;

  @Column({ name: 'full_text', type: 'text', default: '' })
  fullText: string;

  @Column({ type: 'jsonb', default: () => "'[]'::jsonb" })
  segments: TranscriptSegmentJson[];

  @Column({ type: 'jsonb', default: () => "'[]'::jsonb" })
  words: TranscriptWordJson[];

  @Column({ name: 'word_count', type: 'int', default: 0 })
  wordCount: number;

  @Column({ name: 'duration_ms', type: 'int', nullable: true })
  durationMs: number | null;

  @Column({ name: 'provider_job_id', type: 'text', nullable: true })
  providerJobId: string | null;

  @Column({ name: 'provider_metadata', type: 'jsonb', nullable: true })
  providerMetadata: Record<string, unknown> | null;

  @Column({ default: 'PENDING' })
  status: string;

  @Column({ name: 'error_code', type: 'text', nullable: true })
  errorCode: string | null;

  @CreateDateColumn({ name: 'created_at', type: 'timestamptz' })
  createdAt: Date;

  @UpdateDateColumn({ name: 'updated_at', type: 'timestamptz' })
  updatedAt: Date;

  @ManyToOne(() => Video, { onDelete: 'CASCADE' })
  @JoinColumn({ name: 'video_id' })
  video: Video;
}

export interface ComponentScores {
  hook: number;
  clarity: number;
  novelty: number;
  emotion: number;
  completeness: number;
  shareability: number;
}

@Index('candidates_video_id_superseded_at_score_idx', ['videoId', 'supersededAt', 'score'])
@Entity('candidates')
export class Candidate {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ name: 'video_id', type: 'uuid' })
  videoId: string;

  @Column({ name: 'transcript_id', type: 'uuid', nullable: true })
  transcriptId: string | null;

  @Column({ name: 'start_ms', type: 'int' })
  startMs: number;

  @Column({ name: 'end_ms', type: 'int' })
  endMs: number;

  @Column()
  title: string;

  @Column({ name: 'hook_text', type: 'text' })
  hookText: string;

  @Column({ type: 'text' })
  excerpt: string;

  @Column({ type: 'text' })
  summary: string;

  @Column({ type: 'text' })
  reason: string;

  @Column()
  category: string;

  @Column({ type: 'int' })
  score: number;

  @Column({ name: 'component_scores', type: 'jsonb' })
  componentScores: ComponentScores;

  @Column({ name: 'analysis_version' })
  analysisVersion: string;

  @Column({ name: 'analysis_run', type: 'int' })
  analysisRun: number;

  @Column({ type: 'text', nullable: true })
  provider: string | null;

  @Column({ type: 'int' })
  rank: number;

  @Column({ name: 'superseded_at', type: 'timestamptz', nullable: true })
  supersededAt: Date | null;

  @CreateDateColumn({ name: 'created_at', type: 'timestamptz' })
  createdAt: Date;

  @ManyToOne(() => Video, (v) => v.candidates, { onDelete: 'CASCADE' })
  @JoinColumn({ name: 'video_id' })
  video: Video;
}

export interface RenderSettingsJson {
  aspectRatio: '9:16';
  framingMode: 'auto' | 'center' | 'fit';
  captions: { enabled: boolean; preset: string };
  output: { width: number; height: number };
}

@Unique('renders_lineage_key_version_key', ['lineageKey', 'version'])
@Index('renders_user_id_created_at_idx', ['userId', 'createdAt'])
@Index('renders_video_id_idx', ['videoId'])
@Index('renders_settings_hash_idx', ['settingsHash'])
@Entity('renders')
export class Render {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ name: 'video_id', type: 'uuid' })
  videoId: string;

  @Column({ name: 'user_id', type: 'uuid' })
  userId: string;

  @Column({ name: 'candidate_id', type: 'uuid', nullable: true })
  candidateId: string | null;

  @Column({ name: 'parent_render_id', type: 'uuid', nullable: true })
  parentRenderId: string | null;

  @Column({ name: 'lineage_key' })
  lineageKey: string;

  @Column({ type: 'int' })
  version: number;

  @Column({ name: 'is_latest', type: 'boolean', default: true })
  isLatest: boolean;

  @Column()
  title: string;

  @Column({ name: 'start_ms', type: 'int' })
  startMs: number;

  @Column({ name: 'end_ms', type: 'int' })
  endMs: number;

  @Column({ type: 'jsonb' })
  settings: RenderSettingsJson;

  @Column({ name: 'settings_hash' })
  settingsHash: string;

  @Column({ name: 'pipeline_version' })
  pipelineVersion: string;

  @Column({ default: 'QUEUED' })
  status: string;

  @Column({ type: 'int', default: 0 })
  progress: number;

  @Column({ type: 'text', nullable: true })
  stage: string | null;

  @Column({ type: 'text', nullable: true })
  substage: string | null;

  @Column({ type: 'int', default: 1 })
  attempt: number;

  @Column({ name: 'output_key', type: 'text', nullable: true })
  outputKey: string | null;

  @Column({ name: 'thumbnail_key', type: 'text', nullable: true })
  thumbnailKey: string | null;

  @Column({ type: 'int', nullable: true })
  width: number | null;

  @Column({ type: 'int', nullable: true })
  height: number | null;

  @Column({ name: 'duration_ms', type: 'int', nullable: true })
  durationMs: number | null;

  @Column({ name: 'file_size', type: 'bigint', nullable: true, transformer: bigintTransformer })
  fileSize: number | null;

  @Column({ type: 'jsonb', nullable: true })
  framing: Record<string, unknown> | null;

  @Column({ name: 'error_code', type: 'text', nullable: true })
  errorCode: string | null;

  @Column({ name: 'error_message', type: 'text', nullable: true })
  errorMessage: string | null;

  @Column({ name: 'error_retryable', type: 'boolean', nullable: true })
  errorRetryable: boolean | null;

  @Column({ name: 'error_correlation_id', type: 'text', nullable: true })
  errorCorrelationId: string | null;

  @Column({ name: 'started_at', type: 'timestamptz', nullable: true })
  startedAt: Date | null;

  @Column({ name: 'completed_at', type: 'timestamptz', nullable: true })
  completedAt: Date | null;

  @Column({ name: 'deleted_at', type: 'timestamptz', nullable: true })
  deletedAt: Date | null;

  @CreateDateColumn({ name: 'created_at', type: 'timestamptz' })
  createdAt: Date;

  @UpdateDateColumn({ name: 'updated_at', type: 'timestamptz' })
  updatedAt: Date;

  @ManyToOne(() => Video, (v) => v.renders, { onDelete: 'CASCADE' })
  @JoinColumn({ name: 'video_id' })
  video: Video;

  @ManyToOne(() => Candidate, { onDelete: 'SET NULL', nullable: true })
  @JoinColumn({ name: 'candidate_id' })
  candidate: Candidate | null;
}

@Unique('job_runs_queue_job_attempt_key', ['queueName', 'externalJobId', 'attempt'])
@Index('job_runs_entity_idx', ['entityType', 'entityId'])
@Entity('job_runs')
export class JobRun {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ name: 'queue_name' })
  queueName: string;

  @Column({ name: 'external_job_id' })
  externalJobId: string;

  @Column({ name: 'entity_type' })
  entityType: string;

  @Column({ name: 'entity_id' })
  entityId: string;

  @Column({ type: 'int' })
  attempt: number;

  @Column()
  status: string;

  @CreateDateColumn({ name: 'started_at', type: 'timestamptz' })
  startedAt: Date;

  @Column({ name: 'completed_at', type: 'timestamptz', nullable: true })
  completedAt: Date | null;

  @Column({ name: 'duration_ms', type: 'int', nullable: true })
  durationMs: number | null;

  @Column({ name: 'error_code', type: 'text', nullable: true })
  errorCode: string | null;

  @Column({ name: 'error_message', type: 'text', nullable: true })
  errorMessage: string | null;

  @Column({ type: 'jsonb', nullable: true })
  metadata: Record<string, unknown> | null;
}

@Index('usage_events_user_id_created_at_idx', ['userId', 'createdAt'])
@Entity('usage_events')
export class UsageEvent {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ name: 'user_id', type: 'uuid' })
  userId: string;

  @Column({ name: 'event_type' })
  eventType: string;

  @Column({ name: 'video_id', type: 'uuid', nullable: true })
  videoId: string | null;

  @Column({ name: 'render_id', type: 'uuid', nullable: true })
  renderId: string | null;

  @Column({ type: 'double precision', transformer: numericTransformer })
  units: number;

  @Column()
  unit: string;

  @Column({ name: 'idempotency_key', unique: true })
  idempotencyKey: string;

  @CreateDateColumn({ name: 'created_at', type: 'timestamptz' })
  createdAt: Date;
}

export const ENTITIES = [User, Video, Transcript, Candidate, Render, JobRun, UsageEvent];
