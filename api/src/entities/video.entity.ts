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
  UpdateDateColumn,
} from 'typeorm';
import { Candidate } from './candidate.entity';
import { Render } from './render.entity';
import { Transcript } from './transcript.entity';
import { bigintTransformer, numericTransformer } from './transformers';
import { User } from './user.entity';

@Index('videos_user_id_created_at_idx', ['user_id', 'createdAt'])
@Index('videos_status_idx', ['status'])
@Entity('videos')
export class Video {
  @PrimaryGeneratedColumn('increment', { type: 'bigint' })
  video_id: string;

  @Column({ type: 'bigint' })
  user_id: string;

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

  @Column({ type: 'bigint', nullable: true, unique: true })
  active_transcript_id: string | null;

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
