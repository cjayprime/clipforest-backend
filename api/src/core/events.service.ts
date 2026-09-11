import { Injectable, Logger, MessageEvent, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import Redis from 'ioredis';
import { filter, interval, map, merge, Observable, Subject } from 'rxjs';
import { config } from '../config';
import { redisOptionsFromUrl } from './redis';

export interface ProgressEvent {
  type: 'video.updated' | 'render.updated';
  userId: string;
  videoId?: string;
  renderId?: string;
  status: string;
  progress?: number;
  stage?: string | null;
  substage?: string | null;
  errorCode?: string | null;
  at: string;
}

/**
 * Realtime progress fan-out. Workers (and the API itself) publish to one Redis
 * channel; each SSE connection receives only its own user's events. Progress is
 * advisory — clients reconstruct authoritative state from GET endpoints.
 */
@Injectable()
export class EventsService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(EventsService.name);
  private readonly subject = new Subject<ProgressEvent>();
  private sub?: Redis;
  private pub?: Redis;

  async onModuleInit() {
    if (config.openapiOnly) return;
    const opts = redisOptionsFromUrl(config.redisUrl);
    this.sub = new Redis(opts);
    this.pub = new Redis(opts);
    this.sub.on('error', (err) => this.logger.warn({ err }, 'Redis subscriber error'));
    this.pub.on('error', (err) => this.logger.warn({ err }, 'Redis publisher error'));
    this.sub.on('message', (_channel, message) => {
      try {
        this.subject.next(JSON.parse(message) as ProgressEvent);
      } catch {
        /* ignore malformed */
      }
    });
    await this.sub.subscribe(config.eventsChannel);
  }

  async publish(event: Omit<ProgressEvent, 'at'>) {
    if (!this.pub) return;
    try {
      await this.pub.publish(config.eventsChannel, JSON.stringify({ ...event, at: new Date().toISOString() }));
    } catch (err) {
      this.logger.warn({ err }, 'Failed to publish progress event');
    }
  }

  streamFor(userId: string): Observable<MessageEvent> {
    const events = this.subject.pipe(
      filter((e) => e.userId === userId),
      map((e) => ({ type: e.type, data: e }) as MessageEvent),
    );
    const heartbeat = interval(25_000).pipe(map(() => ({ type: 'ping', data: { at: new Date().toISOString() } }) as MessageEvent));
    return merge(events, heartbeat);
  }

  async onModuleDestroy() {
    this.subject.complete();
    await Promise.all([this.sub?.quit(), this.pub?.quit()].filter(Boolean));
  }
}
