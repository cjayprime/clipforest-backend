import { Module } from '@nestjs/common';
import { BillingModule } from '../billing/billing.module';
import { RendersController } from './renders.controller';
import { RendersService } from './renders.service';

@Module({
  imports: [BillingModule],
  controllers: [RendersController],
  providers: [RendersService],
})
export class RendersModule {}
