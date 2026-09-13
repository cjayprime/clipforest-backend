import {
  Column,
  CreateDateColumn,
  Entity,
  Index,
  JoinColumn,
  ManyToOne,
  PrimaryGeneratedColumn,
  UpdateDateColumn,
} from 'typeorm';
import { User } from './user.entity';

/**
 * A Polar subscription, mirrored locally so the app never has to call Polar to
 * answer "what plan is this user on". Polar remains the source of truth for
 * billing; this row is a projection kept current by webhooks.
 */
@Index('subscriptions_user_id_idx', ['user_id'])
@Entity('subscriptions')
export class Subscription {
  @PrimaryGeneratedColumn('increment', { type: 'bigint' })
  subscription_id: string;

  @Column({ type: 'bigint' })
  user_id: string;

  /** Polar's own subscription id — unique, and what webhooks are matched on. */
  @Column({ name: 'polar_subscription_id', unique: true })
  polarSubscriptionId: string;

  @Column({ name: 'polar_customer_id' })
  polarCustomerId: string;

  @Column({ name: 'polar_product_id' })
  polarProductId: string;

  /** starter | creator | studio, resolved from the product id. */
  @Column()
  plan: string;

  /** active | canceled | past_due | paused (Polar's vocabulary). */
  @Column()
  status: string;

  /** month | year. Both grant the same credits every month. */
  @Column({ name: 'recurring_interval' })
  recurringInterval: string;

  /** Minor units, as Polar reports them. */
  @Column({ type: 'int', nullable: true })
  amount: number | null;

  @Column({ type: 'text', nullable: true })
  currency: string | null;

  @Column({ name: 'current_period_start', type: 'timestamptz', nullable: true })
  currentPeriodStart: Date | null;

  @Column({ name: 'current_period_end', type: 'timestamptz', nullable: true })
  currentPeriodEnd: Date | null;

  @Column({ name: 'cancel_at_period_end', type: 'boolean', default: false })
  cancelAtPeriodEnd: boolean;

  @Column({ name: 'started_at', type: 'timestamptz', nullable: true })
  startedAt: Date | null;

  @Column({ name: 'ends_at', type: 'timestamptz', nullable: true })
  endsAt: Date | null;

  @CreateDateColumn({ name: 'created_at', type: 'timestamptz' })
  createdAt: Date;

  @UpdateDateColumn({ name: 'updated_at', type: 'timestamptz' })
  updatedAt: Date;

  @ManyToOne(() => User, { onDelete: 'CASCADE' })
  @JoinColumn({ name: 'user_id' })
  user: User;
}
