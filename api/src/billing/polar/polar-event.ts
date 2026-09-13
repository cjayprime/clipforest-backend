/**
 * The shapes Polar sends to our webhook endpoint. Only the fields we actually
 * read are declared; Polar may send more and that is fine.
 *
 * Schemas confirmed against Polar's OpenAPI document (2026-04).
 */

/** Every delivery is `{ type, data }`. */
export interface PolarWebhookEvent<T = unknown> {
  type: string;
  data: T;
}

/** `billing_reason` on an order. Confirmed enum — note there is no `subscription_create`. */
export type PolarBillingReason = 'purchase' | 'subscription_update' | 'subscription_cycle';

export interface PolarOrder {
  id: string;
  status: 'pending' | 'completed' | 'failed' | 'refunded';
  paid?: boolean;
  amount?: number;
  currency?: string;
  customer_id: string;
  product_id: string;
  /** Null for one-off purchases; present for anything subscription-related. */
  subscription_id?: string | null;
  checkout_id?: string | null;
  billing_reason?: PolarBillingReason;
  metadata?: Record<string, unknown> | null;
  created_at?: string;
}

export type PolarSubscriptionStatus = 'active' | 'canceled' | 'past_due' | 'paused';

export interface PolarSubscription {
  id: string;
  status: PolarSubscriptionStatus;
  amount?: number | null;
  currency?: string | null;
  recurring_interval: 'month' | 'year';
  current_period_start?: string | null;
  current_period_end?: string | null;
  cancel_at_period_end?: boolean;
  started_at?: string | null;
  ends_at?: string | null;
  customer_id: string;
  product_id: string;
  metadata?: Record<string, unknown> | null;
}

/**
 * Which paid orders earn a monthly allowance.
 *
 * `purchase` is the FIRST paid order on a new subscription, and
 * `subscription_cycle` is each renewal — granting on renewals alone would skip
 * the customer's first month entirely. `subscription_update` is a mid-cycle plan
 * change and is excluded: that is proration, not a fresh month.
 */
export const GRANTING_BILLING_REASONS: readonly PolarBillingReason[] = ['purchase', 'subscription_cycle'];

export function grantsAllowance(order: PolarOrder): boolean {
  if (!order.subscription_id) return false;
  if (!order.billing_reason) return false;
  return GRANTING_BILLING_REASONS.includes(order.billing_reason);
}
