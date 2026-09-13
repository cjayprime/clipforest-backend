import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { DataSource, EntityManager, Repository } from 'typeorm';
import { config } from '../config';
import { Errors, isUniqueViolation } from '../common/errors';
import { EventsService } from '../core/events.service';
import { CreditBalance, CreditLedgerEntry, ProcessedWebhook, Subscription } from '../entities';
import { applyGrant, totalCredits, type Balance } from './credits';
import type { PlanId } from './plans';
import type { PolarSubscription } from './polar/polar-event';

export type GrantOutcome = 'granted' | 'duplicate';

@Injectable()
export class BillingService {
  private readonly logger = new Logger(BillingService.name);

  constructor(
    @InjectRepository(CreditBalance) private readonly balances: Repository<CreditBalance>,
    @InjectRepository(Subscription) private readonly subscriptions: Repository<Subscription>,
    @InjectRepository(ProcessedWebhook) private readonly webhooks: Repository<ProcessedWebhook>,
    private readonly dataSource: DataSource,
    private readonly events: EventsService,
  ) {}

  async balanceFor(userId: string): Promise<Balance> {
    const row = await this.balances.findOne({ where: { user_id: userId } });
    return { rollover: row?.rolloverCredits ?? 0, expiring: row?.expiringCredits ?? 0 };
  }

  async subscriptionFor(userId: string): Promise<Subscription | null> {
    return this.subscriptions.findOne({ where: { user_id: userId }, order: { createdAt: 'DESC' } });
  }

  /**
   * Whether the paid order from a checkout has been turned into credits. Scoped
   * to the user, so one customer can never learn about another's checkout.
   */
  async checkoutCredited(userId: string, checkoutId: string): Promise<boolean> {
    return this.dataSource
      .getRepository(CreditLedgerEntry)
      .createQueryBuilder('entry')
      .where('entry.user_id = :userId', { userId })
      .andWhere("entry.metadata ->> 'checkoutId' = :checkoutId", { checkoutId })
      .getExists();
  }

  /** Used to match an order back to a user when the order carries no metadata. */
  async subscriptionForPolarId(polarSubscriptionId: string): Promise<Subscription | null> {
    return this.subscriptions.findOne({ where: { polarSubscriptionId } });
  }

  /** Locks the balance row for the rest of the transaction, creating it if absent. */
  private async lockBalance(tx: EntityManager, userId: string): Promise<CreditBalance> {
    const repo = tx.getRepository(CreditBalance);
    const existing = await repo.findOne({ where: { user_id: userId }, lock: { mode: 'pessimistic_write' } });
    if (existing) return existing;
    try {
      await repo.insert({ user_id: userId, rolloverCredits: 0, expiringCredits: 0 });
    } catch (err) {
      // A concurrent request created it first; fall through and read it back.
      if (!isUniqueViolation(err)) throw err;
    }
    const created = await repo.findOne({ where: { user_id: userId }, lock: { mode: 'pessimistic_write' } });
    if (!created) throw new Error('credit balance row disappeared');
    return created;
  }

  private async write(
    tx: EntityManager,
    userId: string,
    row: CreditBalance,
    next: Balance,
    entry: { reason: string; idempotencyKey: string; metadata?: Record<string, unknown> },
  ) {
    // The ledger row goes first: its unique idempotency key is what makes the
    // whole operation replay-safe, and a violation must abort before any balance
    // moves. save() rather than insert() because insert()'s QueryDeepPartialEntity
    // cannot express a jsonb column typed as a plain record; this is still a single
    // INSERT on a new entity, so a duplicate key still raises 23505.
    const ledger = tx.getRepository(CreditLedgerEntry);
    await ledger.save(
      ledger.create({
        user_id: userId,
        reason: entry.reason,
        rolloverDelta: next.rollover - row.rolloverCredits,
        expiringDelta: next.expiring - row.expiringCredits,
        rolloverAfter: next.rollover,
        expiringAfter: next.expiring,
        idempotencyKey: entry.idempotencyKey,
        metadata: entry.metadata ?? null,
      }),
    );
    await tx
      .getRepository(CreditBalance)
      .update({ credit_balance_id: row.credit_balance_id }, { rolloverCredits: next.rollover, expiringCredits: next.expiring });
  }

  /**
   * Adds a period's allowance. Idempotent on `idempotencyKey` — Polar delivers
   * webhooks at least once, so the same order WILL arrive twice.
   */
  async grant(
    userId: string,
    credits: number,
    idempotencyKey: string,
    metadata?: Record<string, unknown>,
  ): Promise<GrantOutcome> {
    try {
      const outcome = await this.dataSource.transaction(async (tx): Promise<GrantOutcome> => {
        const row = await this.lockBalance(tx, userId);
        // Repeats are routine — Polar redelivers, and the annual job re-offers every
        // month it has already granted on each run. Answering them with a lookup
        // under the balance lock keeps them out of the database error log; the
        // unique key below remains the backstop for a genuine race.
        if (await tx.getRepository(CreditLedgerEntry).exists({ where: { idempotencyKey } })) return 'duplicate';
        const next = applyGrant(
          { rollover: row.rolloverCredits, expiring: row.expiringCredits },
          credits,
          config.credits.rolloverShare,
        );
        await this.write(tx, userId, row, next, { reason: 'grant', idempotencyKey, metadata });
        return 'granted';
      });
      if (outcome === 'duplicate') {
        this.logger.debug({ userId, idempotencyKey }, 'Credit grant already applied; ignoring repeat');
        return 'duplicate';
      }
      // Published after the commit, never inside it: a transaction that rolls back
      // must not announce credits that do not exist. Repeats return above without
      // reaching here, so a grant is announced exactly once.
      await this.events.publish({ type: 'credits.updated', userId, status: 'GRANTED' });
      return 'granted';
    } catch (err) {
      if (isUniqueViolation(err)) {
        this.logger.log({ userId, idempotencyKey }, 'Credit grant lost a race to an identical grant; ignoring');
        return 'duplicate';
      }
      throw err;
    }
  }

  /**
   * Throws BILLING_INSUFFICIENT_CREDITS when the balance cannot cover `credits`.
   * A no-op unless CREDITS_ENFORCED. Never debits: the worker debits when the
   * work starts and refunds it on failure.
   */
  async assertCanAfford(userId: string, credits: number): Promise<void> {
    if (!config.credits.enforced || credits <= 0) return;
    const available = totalCredits(await this.balanceFor(userId));
    if (available < credits) throw Errors.insufficientCredits(credits, available);
  }

  /** Mirrors Polar's subscription onto our projection. */
  async upsertSubscription(userId: string, plan: PlanId, sub: PolarSubscription): Promise<void> {
    const fields = {
      user_id: userId,
      polarSubscriptionId: sub.id,
      polarCustomerId: sub.customer_id,
      polarProductId: sub.product_id,
      plan,
      status: sub.status,
      recurringInterval: sub.recurring_interval,
      amount: sub.amount ?? null,
      currency: sub.currency ?? null,
      currentPeriodStart: sub.current_period_start ? new Date(sub.current_period_start) : null,
      currentPeriodEnd: sub.current_period_end ? new Date(sub.current_period_end) : null,
      cancelAtPeriodEnd: sub.cancel_at_period_end ?? false,
      startedAt: sub.started_at ? new Date(sub.started_at) : null,
      endsAt: sub.ends_at ? new Date(sub.ends_at) : null,
    };
    const existing = await this.subscriptions.findOne({ where: { polarSubscriptionId: sub.id } });
    if (existing) {
      await this.subscriptions.update({ subscription_id: existing.subscription_id }, fields);
    } else {
      try {
        await this.subscriptions.insert(fields);
      } catch (err) {
        // Raced with another delivery of the same event: the row now exists.
        if (!isUniqueViolation(err)) throw err;
        await this.subscriptions.update({ polarSubscriptionId: sub.id }, fields);
      }
    }
    // Published after the write, like a grant.
    await this.events.publish({ type: 'subscription.updated', userId, status: sub.status });
  }

  /**
   * Records a delivery. Returns false when this event was already handled, which
   * is the outer guard against Polar's at-least-once delivery.
   */
  async claimWebhook(provider: string, eventId: string, eventType: string): Promise<boolean> {
    // Redeliveries are expected, so answer them with a lookup rather than a
    // failed insert that the database logger would report as an error.
    if (await this.webhooks.exists({ where: { provider, eventId } })) return false;
    try {
      await this.webhooks.insert({ provider, eventId, eventType });
      return true;
    } catch (err) {
      if (isUniqueViolation(err)) return false;
      throw err;
    }
  }
}
