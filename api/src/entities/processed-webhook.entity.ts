import { Column, CreateDateColumn, Entity, PrimaryGeneratedColumn, Unique } from 'typeorm';

/**
 * Every webhook we have already handled. Polar delivers at least once, so the
 * same event id can arrive repeatedly; this is the outer guard, and the ledger's
 * unique idempotency key is the inner one that actually protects the money.
 */
@Unique('processed_webhooks_provider_event_key', ['provider', 'eventId'])
@Entity('processed_webhooks')
export class ProcessedWebhook {
  @PrimaryGeneratedColumn('increment', { type: 'bigint' })
  processed_webhook_id: string;

  /** 'polar' today; the table is provider-agnostic. */
  @Column()
  provider: string;

  /** The delivery's `webhook-id` header. */
  @Column({ name: 'event_id' })
  eventId: string;

  @Column({ name: 'event_type' })
  eventType: string;

  @CreateDateColumn({ name: 'created_at', type: 'timestamptz' })
  createdAt: Date;
}
