import { Column, CreateDateColumn, Entity, Index, PrimaryGeneratedColumn } from 'typeorm';

/**
 * Append-only history of every credit movement. `idempotency_key` is unique, so
 * a webhook redelivered by Polar — which it will be, deliveries are at-least-once
 * — can never grant the same allowance twice.
 */
@Index('credit_ledger_entries_user_id_created_at_idx', ['user_id', 'createdAt'])
@Entity('credit_ledger_entries')
export class CreditLedgerEntry {
  @PrimaryGeneratedColumn('increment', { type: 'bigint' })
  credit_ledger_entry_id: string;

  @Column({ type: 'bigint' })
  user_id: string;

  /**
   * grant | spend | refund | adjustment. Grants are written by the API (Polar
   * webhooks, the annual allowance job); spends and refunds by the worker.
   */
  @Column()
  reason: string;

  /** Signed: positive for a grant, negative for a spend. */
  @Column({ name: 'rollover_delta', type: 'int' })
  rolloverDelta: number;

  @Column({ name: 'expiring_delta', type: 'int' })
  expiringDelta: number;

  /** Balances after this entry was applied, so history can be read without replaying. */
  @Column({ name: 'rollover_after', type: 'int' })
  rolloverAfter: number;

  @Column({ name: 'expiring_after', type: 'int' })
  expiringAfter: number;

  /**
   * e.g. "polar-order:<order id>", "annual-allowance:<sub>:<period>:<month>",
   * "charge:render:<id>:<n>" or "refund:video:<id>:<n>".
   */
  @Column({ name: 'idempotency_key', unique: true })
  idempotencyKey: string;

  @Column({ type: 'jsonb', nullable: true })
  metadata: Record<string, unknown> | null;

  @CreateDateColumn({ name: 'created_at', type: 'timestamptz' })
  createdAt: Date;
}
