import { Controller, Get, Headers, MessageEvent, Res, Sse } from '@nestjs/common';
import { ApiExcludeEndpoint, ApiTags } from '@nestjs/swagger';
import { SkipThrottle } from '@nestjs/throttler';
import type { Response } from 'express';
import { Observable } from 'rxjs';
import { DataSource } from 'typeorm';
import { config } from '../config';
import { purchasableIntervals } from '../billing/polar/product-plan-map';
import { AuthUser, CurrentUser, Public } from '../common/decorators';
import { AppError } from '../common/errors';
import { ASPECT_RATIO_KEYS, CAPTION_PRESETS, FRAMING_MODES } from '../common/render-settings';
import { EventsService } from '../core/events.service';
import { MetricsService } from '../core/metrics.service';
import { QueueService } from '../core/queue.service';
import { StorageService } from '../core/storage.service';

@ApiTags('system')
@Controller()
export class SystemController {
  constructor(
    private readonly dataSource: DataSource,
    private readonly queues: QueueService,
    private readonly storage: StorageService,
    private readonly events: EventsService,
    private readonly metrics: MetricsService,
  ) {}

  @Public()
  @SkipThrottle()
  @Get('health')
  health() {
    return { status: 'ok', time: new Date().toISOString() };
  }

  @Public()
  @SkipThrottle()
  @Get('health/ready')
  async ready(@Res({ passthrough: true }) res: Response) {
    const [db, redis, storage] = await Promise.all([
      this.dataSource
        .query('SELECT 1')
        .then(() => true)
        .catch(() => false),
      this.queues.ping(),
      this.storage.check(),
    ]);
    const ok = db && redis && storage;
    res.status(ok ? 200 : 503);
    return { status: ok ? 'ready' : 'degraded', checks: { db, redis, storage } };
  }

  /** Public product limits so the UI can show file requirements and presets. */
  @Public()
  @Get('config')
  publicConfig() {
    return {
      maxUploadBytes: config.maxUploadBytes,
      maxVideoDurationSec: config.maxVideoDurationSec,
      acceptedExtensions: ['mp4', 'mov', 'webm', 'm4v', 'mkv'],
      sourceHosts: config.allowedSourceHosts,
      captionPresets: CAPTION_PRESETS,
      framingModes: FRAMING_MODES,
      aspectRatios: ASPECT_RATIO_KEYS,
      minCandidateScore: config.minCandidateScore,
      renderMinDurationMs: config.renderMinDurationMs,
      renderMaxDurationMs: config.renderMaxDurationMs,
      pipelineVersion: config.pipelineVersion,
      // False when no Polar access token is configured (checkout answers BILLING_NOT_CONFIGURED).
      billingEnabled: Boolean(config.polar.accessToken),
      // CREDIT_ROLLOVER_SHARE.
      creditRolloverShare: config.credits.rolloverShare,
      // Billing intervals every paid plan can be checked out on.
      billingIntervals: purchasableIntervals(),
      // Credit costs of processing and rendering.
      creditCosts: {
        enforced: config.credits.enforced,
        perSourceMinute: config.credits.costPerSourceMinute,
        perRender: config.credits.costPerRender,
      },
    };
  }

  /** Per-user realtime progress stream (SSE). Clients fall back to polling GET endpoints. */
  @SkipThrottle()
  @Sse('events')
  stream(@CurrentUser() user: AuthUser): Observable<MessageEvent> {
    return this.events.streamFor(user.id);
  }

  @Public()
  @SkipThrottle()
  @ApiExcludeEndpoint()
  @Get('metrics')
  async prometheus(@Headers('authorization') auth: string | undefined, @Res() res: Response) {
    if (config.metricsToken && auth !== `Bearer ${config.metricsToken}`) {
      throw new AppError('AUTH_REQUIRED', 'Metrics token required.', 401);
    }
    res.setHeader('Content-Type', this.metrics.registry.contentType);
    res.send(await this.metrics.registry.metrics());
  }
}
