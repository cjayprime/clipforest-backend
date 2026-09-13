import { Column, CreateDateColumn, Entity, Index, PrimaryGeneratedColumn } from 'typeorm';
import { numericTransformer } from './transformers';

/**
 * Append-only billing ledger. `idempotency_key` is unique, so a retried job can
 * never double-count, and rows outlive the content they refer to.
 */
@Index('usage_events_user_id_created_at_idx', ['user_id', 'createdAt'])
@Entity('usage_events')
export class UsageEvent {
  @PrimaryGeneratedColumn('increment', { type: 'bigint' })
  usage_event_id: string;

  @Column({ type: 'bigint' })
  user_id: string;

  @Column({ name: 'event_type' })
  eventType: string;

  @Column({ type: 'bigint', nullable: true })
  video_id: string | null;

  @Column({ type: 'bigint', nullable: true })
  render_id: string | null;

  @Column({ type: 'double precision', transformer: numericTransformer })
  units: number;

  @Column()
  unit: string;

  @Column({ name: 'idempotency_key', unique: true })
  idempotencyKey: string;

  @CreateDateColumn({ name: 'created_at', type: 'timestamptz' })
  createdAt: Date;
}
