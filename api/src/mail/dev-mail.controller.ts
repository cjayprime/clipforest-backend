import { Controller, Get } from '@nestjs/common';
import { ApiExcludeController } from '@nestjs/swagger';
import { Public } from '../common/decorators';
import { Errors } from '../common/errors';
import { MailService } from './mail.service';

/**
 * Local development and automated tests only.
 *
 * Password reset tokens exist only inside the email, so without a way to read
 * what was "sent" the flow cannot be driven end to end. This returns the recent
 * messages held in memory by MailService.
 *
 * Enabled only when MAIL_TEST_INBOX=1, and config forces it off whenever
 * NODE_ENV=production, so it cannot be switched on by accident in a deployment.
 * It is excluded from the OpenAPI document for the same reason.
 */
@ApiExcludeController()
@Controller('dev/mail')
export class DevMailController {
  constructor(private readonly mail: MailService) {}

  @Public()
  @Get()
  list() {
    if (!this.mail.testInboxEnabled) throw Errors.notFound('Endpoint');
    return { provider: this.mail.provider, items: this.mail.inbox() };
  }
}
