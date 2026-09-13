import { Column, CreateDateColumn, Entity, Index, JoinColumn, ManyToOne, PrimaryGeneratedColumn } from 'typeorm';
import { Video } from './video.entity';

/**
 * The six 0–10 axes the highlight LLM scores a moment on. The weighted 0–100
 * total lives on `Candidate.score`; these are kept so a score can be explained.
 */
export interface ComponentScores {
  hook: number;
  clarity: number;
  novelty: number;
  emotion: number;
  completeness: number;
  shareability: number;
}

@Index('candidates_video_id_superseded_at_score_idx', ['video_id', 'supersededAt', 'score'])
@Entity('candidates')
export class Candidate {
  @PrimaryGeneratedColumn('increment', { type: 'bigint' })
  candidate_id: string;

  @Column({ type: 'bigint' })
  video_id: string;

  @Column({ type: 'bigint', nullable: true })
  transcript_id: string | null;

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
