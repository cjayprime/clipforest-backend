import { Module } from '@nestjs/common';
import { AnnualAllowanceScheduler } from './annual-allowance.scheduler';
import { BillingController } from './billing.controller';
import { BillingService } from './billing.service';
import { PolarService } from './polar/polar.service';
import { PolarWebhookController } from './polar/polar-webhook.controller';

@Module({
  controllers: [BillingController, PolarWebhookController],
  providers: [BillingService, PolarService, AnnualAllowanceScheduler],
  exports: [BillingService],
})
export class BillingModule {}
