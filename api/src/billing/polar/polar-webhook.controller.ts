import { Controller, Headers, HttpCode, Logger, Post, Req } from '@nestjs/common';
import { ApiExcludeController } from '@nestjs/swagger';
import { SkipThrottle } from '@nestjs/throttler';
import type { RawBodyRequest } from '@nestjs/common';
import type { Request } from 'express';
import { config } from '../../config';
import { Public } from '../../common/decorators';
import { Errors } from '../../common/errors';
import { PLANS } from '../plans';
import { BillingService } from '../billing.service';
import { grantsAllowance, type PolarOrder, type PolarSubscription, type PolarWebhookEvent } from './polar-event';
import { planForProduct } from './product-plan-map';
import { verifyWebhookSignature } from './verify-webhook';

@ApiExcludeController()
@Controller('billing/webhooks')
export class PolarWebhookController {
  private readonly logger = new Logger(PolarWebhookController.name);

  constructor(private readonly billing: BillingService) {}

  /**
   * Polar retries anything that is not 2xx, so this answers 202 for everything
   * it has durably handled, including events it ignores.
   */
  @Public()
  @SkipThrottle()
  @Post('polar')
  @HttpCode(202)
  async polar(
    @Req() req: RawBodyRequest<Request>,
    @Headers('webhook-id') id: string | undefined,
    @Headers('webhook-timestamp') timestamp: string | undefined,
    @Headers('webhook-signature') signature: string | undefined,
  ) {
    // An unsigned webhook that grants credits would let anyone mint themselves a
    // paid plan, so a missing secret refuses every delivery rather than trusting it.
    if (!config.polar.webhookSecret) {
      this.logger.error('POLAR_WEBHOOK_SECRET is not set; refusing the delivery');
      throw Errors.invalidWebhookSignature();
    }
    // The signature covers the exact bytes received; the parsed body is no use here.
    const raw = req.rawBody;
    if (!raw) {
      this.logger.error('Raw body unavailable — NestFactory needs rawBody: true');
      throw Errors.invalidWebhookSignature();
    }
    const ok = verifyWebhookSignature({
      secret: config.polar.webhookSecret,
      headers: { id, timestamp, signature },
      body: raw,
      toleranceSec: config.polar.webhookToleranceSec,
    });
    if (!ok) throw Errors.invalidWebhookSignature();

    let event: PolarWebhookEvent;
    try {
      event = JSON.parse(raw.toString('utf8')) as PolarWebhookEvent;
    } catch {
      // Signed but unparseable: retrying will not help.
      this.logger.error('Signed webhook body was not JSON');
      return { ok: true, ignored: 'unparseable' };
    }

    const fresh = await this.billing.claimWebhook('polar', id!, event.type);
    if (!fresh) return { ok: true, ignored: 'duplicate' };

    if (event.type === 'order.paid') return this.onOrderPaid(event.data as PolarOrder);
    if (event.type.startsWith('subscription.')) return this.onSubscription(event.data as PolarSubscription);
    return { ok: true, ignored: event.type };
  }

  private async onOrderPaid(order: PolarOrder) {
    if (!grantsAllowance(order)) {
      // subscription_update is a mid-cycle plan change: proration, not a new month.
      return { ok: true, ignored: `billing_reason=${order.billing_reason ?? 'none'}` };
    }
    const match = planForProduct(order.product_id);
    if (!match) {
      // An unmapped product must never fall back to a default allowance.
      this.logger.error({ productId: order.product_id }, 'Paid order for a product with no configured plan');
      return { ok: true, ignored: 'unmapped-product' };
    }
    // Monthly and annual orders both grant one month here. For annual plans this
    // is month 0; AnnualAllowanceScheduler tops up the remaining eleven.
    const { plan, interval } = match;
    const userId = await this.userIdForOrder(order);
    if (!userId) {
      this.logger.error({ orderId: order.id }, 'Paid order could not be matched to a user');
      return { ok: true, ignored: 'unmatched-user' };
    }
    const outcome = await this.billing.grant(userId, PLANS[plan].monthlyCredits, `polar-order:${order.id}`, {
      plan,
      interval,
      orderId: order.id,
      billingReason: order.billing_reason,
      subscriptionId: order.subscription_id,
      // Read by GET /billing/checkout/:checkoutId.
      checkoutId: order.checkout_id ?? null,
    });
    this.logger.log({ userId, plan, orderId: order.id, outcome }, 'Credit grant from paid order');
    return { ok: true, outcome };
  }

  private async onSubscription(sub: PolarSubscription) {
    const match = planForProduct(sub.product_id);
    if (!match) return { ok: true, ignored: 'unmapped-product' };
    const { plan } = match;
    const userId = typeof sub.metadata?.userId === 'string' ? sub.metadata.userId : null;
    if (!userId) {
      this.logger.error({ subscriptionId: sub.id }, 'Subscription event carried no userId metadata');
      return { ok: true, ignored: 'unmatched-user' };
    }
    await this.billing.upsertSubscription(userId, plan, sub);
    return { ok: true };
  }

  /**
   * Orders carry our user id through the checkout's metadata; the subscription
   * projection is the fallback when they do not.
   */
  private async userIdForOrder(order: PolarOrder): Promise<string | null> {
    const fromMetadata = order.metadata?.userId;
    if (typeof fromMetadata === 'string' && fromMetadata) return fromMetadata;
    if (!order.subscription_id) return null;
    const sub = await this.billing.subscriptionForPolarId(order.subscription_id);
    return sub?.user_id ?? null;
  }
}
