import { IsIn, IsOptional } from 'class-validator';
import type { BillingInterval, PlanId } from './plans';

export class StartCheckoutDto {
  @IsIn(['starter', 'creator', 'studio'])
  plan: Exclude<PlanId, 'free'>;

  /** Defaults to monthly. Annual is a separate Polar product per plan. */
  @IsOptional()
  @IsIn(['month', 'year'])
  interval?: BillingInterval;
}
