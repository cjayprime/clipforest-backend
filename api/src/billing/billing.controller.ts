import { Body, Controller, Get, HttpCode, Param, Post } from '@nestjs/common';
import { ApiOperation, ApiTags } from '@nestjs/swagger';
import { config } from '../config';
import { AuthUser, CurrentUser } from '../common/decorators';
import { Errors } from '../common/errors';
import { BillingService } from './billing.service';
import { totalCredits } from './credits';
import { StartCheckoutDto } from './billing.dto';
import { PAID_PLANS, PLANS } from './plans';
import { PolarService } from './polar/polar.service';
import { productForPlan } from './polar/product-plan-map';

/** Polar's checkout ids. Anything else — including an unsubstituted placeholder — is not one. */
const CHECKOUT_ID = /^[A-Za-z0-9_-]{1,100}$/;

@ApiTags('billing')
@Controller('billing')
export class BillingController {
  constructor(
    private readonly billing: BillingService,
    private readonly polar: PolarService,
  ) {}

  @Get('balance')
  @ApiOperation({ summary: 'Credit balance and current plan' })
  async balance(@CurrentUser() user: AuthUser) {
    const [balance, subscription] = await Promise.all([
      this.billing.balanceFor(user.id),
      this.billing.subscriptionFor(user.id),
    ]);
    return {
      credits: { rollover: balance.rollover, expiring: balance.expiring, total: totalCredits(balance) },
      rolloverShare: config.credits.rolloverShare,
      plan: subscription?.plan ?? 'free',
      subscription: subscription
        ? {
            status: subscription.status,
            recurringInterval: subscription.recurringInterval,
            currentPeriodEnd: subscription.currentPeriodEnd,
            cancelAtPeriodEnd: subscription.cancelAtPeriodEnd,
          }
        : null,
      plans: PAID_PLANS.map((p) => ({ id: p.id, name: p.name, monthlyCredits: p.monthlyCredits })),
    };
  }

  @Post('checkout')
  @HttpCode(200)
  @ApiOperation({ summary: 'Start a Polar checkout for a plan and billing interval' })
  async checkout(@CurrentUser() user: AuthUser, @Body() dto: StartCheckoutDto) {
    const interval = dto.interval ?? 'month';
    const productId = productForPlan(dto.plan, interval);
    if (!productId) throw Errors.planUnavailable(PLANS[dto.plan].name, interval);
    const checkout = await this.polar.createCheckout({
      productId,
      userId: user.id,
      email: user.email,
      successUrl: new URL('/settings?checkout={CHECKOUT_ID}', config.publicWebUrl).toString(),
    });
    return { url: checkout.url, plan: dto.plan, interval };
  }

  /**
   * Whether the paid order from a checkout has been credited. Polar redirects to
   * /settings?checkout=<id> when payment is taken; credits arrive later with the
   * paid-order webhook.
   */
  @Get('checkout/:checkoutId')
  @ApiOperation({ summary: 'Whether a completed checkout has been credited yet' })
  async checkoutStatus(@CurrentUser() user: AuthUser, @Param('checkoutId') checkoutId: string) {
    if (!CHECKOUT_ID.test(checkoutId)) throw Errors.notFound('Checkout');
    const credited = await this.billing.checkoutCredited(user.id, checkoutId);
    return { checkoutId, status: credited ? ('credited' as const) : ('pending' as const) };
  }

  @Post('portal')
  @HttpCode(200)
  @ApiOperation({ summary: 'A short-lived link to manage or cancel the subscription' })
  async portal(@CurrentUser() user: AuthUser) {
    const subscription = await this.billing.subscriptionFor(user.id);
    if (!subscription) throw Errors.noSubscription();
    return { url: await this.polar.createPortalUrl(subscription.polarCustomerId) };
  }
}
