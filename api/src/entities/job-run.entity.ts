import { Column, CreateDateColumn, Entity, Index, PrimaryGeneratedColumn, Unique } from 'typeorm';

/** One attempt at one BullMQ job, for observability and the janitor's self-healing. */
@Unique('job_runs_queue_job_attempt_key', ['queueName', 'externalJobId', 'attempt'])
@Index('job_runs_entity_idx', ['entityType', 'entityId'])
@Entity('job_runs')
export class JobRun {
  @PrimaryGeneratedColumn('increment', { type: 'bigint' })
  job_run_id: string;

  @Column({ name: 'queue_name' })
  queueName: string;

  @Column({ name: 'external_job_id' })
  externalJobId: string;

  @Column({ name: 'entity_type' })
  entityType: string;

  /** Text rather than bigint: rows outlive the entity they point at. */
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
