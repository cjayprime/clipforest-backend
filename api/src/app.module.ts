import { Module } from '@nestjs/common';
import { APP_GUARD, APP_INTERCEPTOR } from '@nestjs/core';
import { ScheduleModule } from '@nestjs/schedule';
import { ThrottlerModule } from '@nestjs/throttler';
import { LoggerModule } from 'nestjs-pino';
import { randomUUID } from 'node:crypto';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { config } from './config';
import { AuthGuard, UserThrottlerGuard } from './auth/auth.guard';
import { AuthModule } from './auth/auth.module';
import { BillingModule } from './billing/billing.module';
import { CoreModule } from './core/core.module';
import { MetricsInterceptor } from './core/metrics.service';
import { MailModule } from './mail/mail.module';
import { RendersModule } from './renders/renders.module';
import { SystemController } from './system/system.controller';
import { VideosModule } from './videos/videos.module';

const CORRELATION_ID = /^[A-Za-z0-9._-]{8,80}$/;

/** Request-log noise: polled by infrastructure, or long-lived by design. */
const UNLOGGED_PATHS = ['/api/health', '/api/metrics', '/api/events'];

@Module({
  imports: [
    LoggerModule.forRoot({
      pinoHttp: {
        level: config.logLevel,
        genReqId: (req: IncomingMessage, res: ServerResponse) => {
          const header = req.headers['x-correlation-id'];
          const id = typeof header === 'string' && CORRELATION_ID.test(header) ? header : randomUUID();
          res.setHeader('x-correlation-id', id);
          return id;
        },
        customProps: (req: IncomingMessage & { user?: { id: string } }) => ({
          correlationId: (req as IncomingMessage & { id?: string }).id,
          userId: req.user?.id,
        }),
        // Never log cookies, tokens or query strings (signed URLs carry credentials in the query).
        serializers: {
          req: (req: { id: string; method: string; url?: string }) => ({
            id: req.id,
            method: req.method,
            url: req.url?.split('?')[0],
          }),
          res: (res: { statusCode: number }) => ({ statusCode: res.statusCode }),
        },
        autoLogging: {
          // Health checks, scrapes and the SSE stream would drown the log.
          ignore: (req: IncomingMessage) => {
            const url = req.url ?? '';
            return UNLOGGED_PATHS.some((prefix) => url.startsWith(prefix));
          },
        },
        transport: config.isProd ? undefined : { target: 'pino-pretty', options: { singleLine: true, colorize: true } },
      },
    }),
    ThrottlerModule.forRoot([{ name: 'default', ttl: 60_000, limit: config.rateLimit.defaultPerMinute }]),
    // Registers every @Cron provider.
    ScheduleModule.forRoot(),
    CoreModule,
    MailModule,
    AuthModule,
    BillingModule,
    VideosModule,
    RendersModule,
  ],
  controllers: [SystemController],
  providers: [
    { provide: APP_GUARD, useClass: AuthGuard },
    { provide: APP_GUARD, useClass: UserThrottlerGuard },
    { provide: APP_INTERCEPTOR, useClass: MetricsInterceptor },
  ],
})
export class AppModule {}
