import {
  Column,
  CreateDateColumn,
  Entity,
  Index,
  JoinColumn,
  ManyToOne,
  PrimaryGeneratedColumn,
  Unique,
  UpdateDateColumn,
} from 'typeorm';
import { Candidate } from './candidate.entity';
import { bigintTransformer } from './transformers';
import { Video } from './video.entity';

/**
 * The immutable parameters a clip was rendered with, stored on `Render.settings`.
 * Changing any of them produces a new version rather than mutating the render.
 */
export interface RenderSettingsJson {
  aspectRatio: '9:16' | '4:5' | '1:1' | '16:9';
  framingMode: 'auto' | 'center' | 'fit';
  captions: { enabled: boolean; preset: string };
  output: { width: number; height: number };
}

@Unique('renders_lineage_key_version_key', ['lineageKey', 'version'])
@Index('renders_user_id_created_at_idx', ['user_id', 'createdAt'])
@Index('renders_video_id_idx', ['video_id'])
@Index('renders_settings_hash_idx', ['settingsHash'])
@Entity('renders')
export class Render {
  @PrimaryGeneratedColumn('increment', { type: 'bigint' })
  render_id: string;

  @Column({ type: 'bigint' })
  video_id: string;

  @Column({ type: 'bigint' })
  user_id: string;

  @Column({ type: 'bigint', nullable: true })
  candidate_id: string | null;

  @Column({ type: 'bigint', nullable: true })
  parent_render_id: string | null;

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
