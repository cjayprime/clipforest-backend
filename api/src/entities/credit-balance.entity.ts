import { Column, Entity, JoinColumn, OneToOne, PrimaryGeneratedColumn, UpdateDateColumn } from 'typeorm';
import { User } from './user.entity';

/**
 * A user's spendable credits, split into two buckets at grant time:
 *
 *   rollover — carries into later months and accumulates
 *   expiring — replaced by each new grant, so unused credits are lost
 *
 * The current totals live here; every change that produced them is recorded in
 * credit_ledger_entries, which is the auditable history.
 */
@Entity('credit_balances')
export class CreditBalance {
  @PrimaryGeneratedColumn('increment', { type: 'bigint' })
  credit_balance_id: string;

  /** One balance per user. */
  @Column({ type: 'bigint', unique: true })
  user_id: string;

  @Column({ name: 'rollover_credits', type: 'int', default: 0 })
  rolloverCredits: number;

  @Column({ name: 'expiring_credits', type: 'int', default: 0 })
  expiringCredits: number;

  @UpdateDateColumn({ name: 'updated_at', type: 'timestamptz' })
  updatedAt: Date;

  @OneToOne(() => User, { onDelete: 'CASCADE' })
  @JoinColumn({ name: 'user_id' })
  user: User;
}
