import { Global, Module } from '@nestjs/common';
import { DevMailController } from './dev-mail.controller';
import { MailService } from './mail.service';

/** Global so any feature module can inject MailService without importing this. */
@Global()
@Module({
  controllers: [DevMailController],
  providers: [MailService],
  exports: [MailService],
})
export class MailModule {}
