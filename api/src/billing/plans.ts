/** The paid plans offered on the pricing page, plus the default for a new account. */
export type PlanId = 'free' | 'starter' | 'creator' | 'studio';

/** How a plan is billed. Both intervals grant the same credits every month. */
export type BillingInterval = 'month' | 'year';

/**
 * A plan as advertised on the pricing page. `monthlyCredits` is granted per
 * billing *month* — an annual subscription is charged once but still topped up
 * monthly. Prices are for display and reconciliation only; the amount charged is
 * whatever the Polar product is configured with.
 */
export interface Plan {
  id: PlanId;
  name: string;
  monthlyCredits: number;
  monthlyPriceUsd: number;
  annualPriceUsdPerMonth: number;
}

export const PLANS: Record<PlanId, Plan> = {
  free: { id: 'free', name: 'Free', monthlyCredits: 0, monthlyPriceUsd: 0, annualPriceUsdPerMonth: 0 },
  starter: { id: 'starter', name: 'Starter', monthlyCredits: 100, monthlyPriceUsd: 15, annualPriceUsdPerMonth: 10 },
  creator: { id: 'creator', name: 'Creator', monthlyCredits: 300, monthlyPriceUsd: 35, annualPriceUsdPerMonth: 25 },
  studio: { id: 'studio', name: 'Studio', monthlyCredits: 1000, monthlyPriceUsd: 99, annualPriceUsdPerMonth: 75 },
};

export const PAID_PLANS: Plan[] = [PLANS.starter, PLANS.creator, PLANS.studio];

const PLAN_IDS: readonly string[] = Object.keys(PLANS);

export function isPlanId(value: string): value is PlanId {
  return PLAN_IDS.includes(value);
}
