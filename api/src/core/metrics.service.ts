import { CallHandler, ExecutionContext, Injectable, NestInterceptor } from '@nestjs/common';
import type { Response } from 'express';
import { collectDefaultMetrics, Counter, Gauge, Histogram, Registry } from 'prom-client';
import { Observable, tap } from 'rxjs';
import type { RequestWithUser } from '../common/decorators';
import { config } from '../config';
import { QueueService } from './queue.service';

/** The Prometheus registry and every metric the API owns. */
@Injectable()
export class MetricsService {
  readonly registry = new Registry();

  readonly httpDuration = new Histogram({
    name: 'cliprover_api_http_request_duration_seconds',
    help: 'API request latency by route',
    labelNames: ['method', 'route', 'status'],
    buckets: [0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
    registers: [this.registry],
  });

  readonly videosCreated = new Counter({
    name: 'cliprover_videos_created_total',
    help: 'Videos created by source type',
    labelNames: ['source_type'],
    registers: [this.registry],
  });

  readonly rendersCreated = new Counter({
    name: 'cliprover_renders_created_total',
    help: 'Render requests by kind (new = first render of a candidate/range, rerender = new version)',
    labelNames: ['kind'],
    registers: [this.registry],
  });

  readonly rendersDeduplicated = new Counter({
    name: 'cliprover_renders_deduplicated_total',
    help: 'Render requests answered by an identical existing render',
    registers: [this.registry],
  });

  constructor(private readonly queues: QueueService) {
    collectDefaultMetrics({ register: this.registry, prefix: 'cliprover_api_' });
    if (config.openapiOnly) return;
    const queueService = this.queues;
    new Gauge({
      name: 'cliprover_queue_jobs',
      help: 'Queue depth by queue and state',
      labelNames: ['queue', 'state'],
      registers: [this.registry],
      async collect() {
        try {
          const counts = await queueService.counts();
          for (const [queue, states] of Object.entries(counts)) {
            for (const [state, n] of Object.entries(states)) this.set({ queue, state }, n);
          }
        } catch {
          /* redis unavailable: leave last values */
        }
      },
    });
  }
}

/** Times every HTTP request into the latency histogram, success or failure. */
@Injectable()
export class MetricsInterceptor implements NestInterceptor {
  constructor(private readonly metrics: MetricsService) {}

  intercept(context: ExecutionContext, next: CallHandler): Observable<unknown> {
    if (context.getType() !== 'http') return next.handle();
    const req = context.switchToHttp().getRequest<RequestWithUser>();
    const res = context.switchToHttp().getResponse<Response>();
    const end = this.metrics.httpDuration.startTimer();
    // The route pattern, not the concrete path: labels must stay low-cardinality.
    // Express types `route` as `any`, so narrow it before reading `path`.
    const matched = req.route as { path?: unknown } | undefined;
    const route = typeof matched?.path === 'string' ? matched.path : 'unknown';
    const done = () => end({ method: req.method, route, status: String(res.statusCode) });
    return next.handle().pipe(tap({ next: done, error: done }));
  }
}
