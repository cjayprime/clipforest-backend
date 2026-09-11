import { CallHandler, ExecutionContext, Injectable, NestInterceptor } from '@nestjs/common';
import { collectDefaultMetrics, Counter, Gauge, Histogram, Registry } from 'prom-client';
import { Observable, tap } from 'rxjs';
import { config } from '../config';
import { QueueService } from './queue.service';

@Injectable()
export class MetricsService {
  readonly registry = new Registry();

  readonly httpDuration = new Histogram({
    name: 'clipforest_api_http_request_duration_seconds',
    help: 'API request latency by route',
    labelNames: ['method', 'route', 'status'],
    buckets: [0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
    registers: [this.registry],
  });

  readonly videosCreated = new Counter({
    name: 'clipforest_videos_created_total',
    help: 'Videos created by source type',
    labelNames: ['source_type'],
    registers: [this.registry],
  });

  readonly rendersCreated = new Counter({
    name: 'clipforest_renders_created_total',
    help: 'Render requests by kind (new = first render of a candidate/range, rerender = new version)',
    labelNames: ['kind'],
    registers: [this.registry],
  });

  readonly rendersDeduplicated = new Counter({
    name: 'clipforest_renders_deduplicated_total',
    help: 'Render requests answered by an identical existing render',
    registers: [this.registry],
  });

  constructor(private readonly queues: QueueService) {
    collectDefaultMetrics({ register: this.registry, prefix: 'clipforest_api_' });
    if (config.openapiOnly) return;
    const queueService = this.queues;
    new Gauge({
      name: 'clipforest_queue_jobs',
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

@Injectable()
export class MetricsInterceptor implements NestInterceptor {
  constructor(private readonly metrics: MetricsService) {}

  intercept(context: ExecutionContext, next: CallHandler): Observable<unknown> {
    if (context.getType() !== 'http') return next.handle();
    const req = context.switchToHttp().getRequest();
    const res = context.switchToHttp().getResponse();
    const end = this.metrics.httpDuration.startTimer();
    const route = req.route?.path ?? 'unknown';
    const done = () => end({ method: req.method, route, status: String(res.statusCode) });
    return next.handle().pipe(tap({ next: done, error: done }));
  }
}
