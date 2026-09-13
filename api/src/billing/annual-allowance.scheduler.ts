import { Injectable, Logger } from '@nestjs/common';
import { Cron } from '@nestjs/schedule';
import { InjectRepository } from '@nestjs/typeorm';
import { IsNull, Not, Repository } from 'typeorm';
import { config } from '../config';
import { Subscription } from '../entities';
import { BillingService } from './billing.service';
import { isPlanId, PLANS } from './plans';

/**
 * Adds whole calendar months in UTC, clamping to the end of a shorter month:
 * 31 January + 1 month is 28 (or 29) February, not 3 March — otherwise an
 * annual subscriber's anniversaries would drift later every year.
 */
export function addCalendarMonths(date: Date, months: number): Date {
  const year = date.getUTCFullYear();
  const month = date.getUTCMonth() + months;
  const lastDay = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  return new Date(
    Date.UTC(
      year,
      month,
      Math.min(date.getUTCDate(), lastDay),
      date.getUTCHours(),
      date.getUTCMinutes(),
      date.getUTCSeconds(),
      date.getUTCMilliseconds(),
    ),
  );
}

/**
 * Which monthly allowances an annual subscription is owed so far this period.
 *
 * Month 0 is granted by Polar's paid-order webhook, so this only returns months
 * 1–11: those whose anniversary has passed and that still fall inside the paid
 * period. Every due month is returned, so a job that was down catches up; each
 * grant is idempotent, so returning one already granted is harmless.
 */
export function dueAnnualMonths(periodStart: Date, periodEnd: Date | null, now: Date): number[] {
  const due: number[] = [];
  for (let month = 1; month < 12; month++) {
    const anniversary = addCalendarMonths(periodStart, month);
    if (anniversary > now) break;
    if (periodEnd && anniversary >= periodEnd) break;
    due.push(month);
  }
  return due;
}

/**
 * Grants annual subscribers their monthly allowance. Polar bills an annual plan
 * once a year, so its webhook only delivers month 0.
 *
 * Safe to run on every API instance at once: each month's grant carries its own
 * idempotency key, so exactly one wins.
 */
@Injectable()
export class AnnualAllowanceScheduler {
  private readonly logger = new Logger(AnnualAllowanceScheduler.name);
  private running = false;

  constructor(
    @InjectRepository(Subscription) private readonly subscriptions: Repository<Subscription>,
    private readonly billing: BillingService,
  ) {}

  @Cron(config.credits.annualAllowanceCron, { name: 'annual-credit-allowance' })
  async handleCron(): Promise<void> {
    // The OpenAPI generator boots with stubbed repositories; a long run must also
    // not overlap the next tick in the same process.
    if (config.openapiOnly || this.running) return;
    this.running = true;
    try {
      const granted = await this.grantDue(new Date());
      if (granted > 0) this.logger.log({ granted }, 'Granted annual subscribers their monthly allowance');
    } finally {
      this.running = false;
    }
  }

  /** Grants every due month and returns how many were newly granted. */
  async grantDue(now: Date): Promise<number> {
    const subscriptions = await this.subscriptions.find({
      where: { recurringInterval: 'year', status: 'active', currentPeriodStart: Not(IsNull()) },
    });
    let granted = 0;
    for (const sub of subscriptions) {
      // One malformed row must not stop everyone else's allowance.
      try {
        granted += await this.grantFor(sub, now);
      } catch (err) {
        this.logger.error({ err, subscriptionId: sub.subscription_id }, 'Annual allowance grant failed');
      }
    }
    return granted;
  }

  private async grantFor(sub: Subscription, now: Date): Promise<number> {
    if (!sub.currentPeriodStart || !isPlanId(sub.plan) || sub.plan === 'free') return 0;
    const periodStart = sub.currentPeriodStart;
    let granted = 0;
    for (const month of dueAnnualMonths(periodStart, sub.currentPeriodEnd, now)) {
      const outcome = await this.billing.grant(
        sub.user_id,
        PLANS[sub.plan].monthlyCredits,
        // The period start scopes the key to one billed year, so a renewal starts
        // a fresh set of months instead of colliding with last year's.
        `annual-allowance:${sub.polarSubscriptionId}:${periodStart.toISOString()}:${String(month)}`,
        { plan: sub.plan, subscriptionId: sub.polarSubscriptionId, month, periodStart: periodStart.toISOString() },
      );
      if (outcome === 'granted') granted++;
    }
    return granted;
  }
}
