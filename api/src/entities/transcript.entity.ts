import {
  Column,
  CreateDateColumn,
  Entity,
  JoinColumn,
  ManyToOne,
  PrimaryGeneratedColumn,
  Unique,
  UpdateDateColumn,
} from 'typeorm';
import { Video } from './video.entity';

/** One word of a transcript, as stored in the `words` jsonb column. */
export interface TranscriptWordJson {
  startMs: number;
  endMs: number;
  text: string;
  confidence?: number;
  speaker?: string | null;
}

/**
 * A sentence-like span, as stored in the `segments` jsonb column. The stable
 * `id` is what the highlight LLM cites, so a proposal can never invent a timestamp.
 */
export interface TranscriptSegmentJson extends TranscriptWordJson {
  id: string;
}

@Unique('transcripts_video_id_version_key', ['video_id', 'version'])
@Entity('transcripts')
export class Transcript {
  @PrimaryGeneratedColumn('increment', { type: 'bigint' })
  transcript_id: string;

  @Column({ type: 'bigint' })
  video_id: string;

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
