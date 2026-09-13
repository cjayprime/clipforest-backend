import { config } from '../../config';
import type { BillingInterval, PlanId } from '../plans';

/** What a Polar product resolves to: which plan, billed how often. */
export interface ProductMatch {
  plan: Exclude<PlanId, 'free'>;
  interval: BillingInterval;
}

const PAID = ['starter', 'creator', 'studio'] as const;
const INTERVALS: readonly BillingInterval[] = ['month', 'year'];

/**
 * Maps a Polar product back to one of our plans and its billing interval. This
 * is the only link between what Polar charged for and how many credits we
 * grant, which is why an unrecognised product must never silently become a
 * default plan.
 */
export function planForProduct(productId: string | undefined | null): ProductMatch | null {
  if (!productId) return null;
  for (const plan of PAID) {
    for (const interval of INTERVALS) {
      const configured = config.polar.products[plan][interval];
      if (configured && configured === productId) return { plan, interval };
    }
  }
  return null;
}

export function productForPlan(plan: PlanId, interval: BillingInterval): string | null {
  if (plan === 'free') return null;
  return config.polar.products[plan][interval] ?? null;
}

/** Billing intervals for which every paid plan has a configured product. */
export function purchasableIntervals(): BillingInterval[] {
  return INTERVALS.filter((interval) => PAID.every((plan) => Boolean(config.polar.products[plan][interval])));
}
