import { Module } from '@nestjs/common';
import { RendersController } from './renders.controller';
import { RendersService } from './renders.service';

@Module({
  controllers: [RendersController],
  providers: [RendersService],
})
export class RendersModule {}
