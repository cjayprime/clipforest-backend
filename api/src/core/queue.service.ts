import { Injectable, Logger, OnModuleDestroy } from '@nestjs/common';
import { Queue } from 'bullmq';
import IORedis from 'ioredis';
import { config } from '../config';
import { jobIds } from '../common/idempotency';
import type { JobsOptions } from 'bullmq';
import { redisOptionsFromUrl } from './redis';

/** Queue names mirror backend/contracts/queues.schema.json. */
const QUEUES = {
  ingest: 'video-ingest',
  transcription: 'transcription',
  analysis: 'analysis',
  render: 'render',
  cleanup: 'cleanup',
} as const;

type QueueKey = keyof typeof QUEUES;

const KEEP = { removeOnComplete: { age: 24 * 3600, count: 5000 }, removeOnFail: { age: 14 * 24 * 3600 } };

/** Retry policy per failure class (PRD §14.4); non-retryable errors are failed immediately by the worker. */
const JOB_DEFAULTS: Record<QueueKey, JobsOptions> = {
  ingest: { attempts: 3, backoff: { type: 'exponential', delay: 10_000 }, ...KEEP },
  transcription: { attempts: 5, backoff: { type: 'exponential', delay: 15_000 }, ...KEEP },
  analysis: { attempts: 3, backoff: { type: 'exponential', delay: 10_000 }, ...KEEP },
  // FFmpeg crash/OOM: one controlled retry after cleanup.
  render: { attempts: 2, backoff: { type: 'exponential', delay: 30_000 }, ...KEEP },
  cleanup: { attempts: 5, backoff: { type: 'exponential', delay: 30_000 }, ...KEEP },
};

/** Payloads carry IDs and immutable parameters only; the worker reads state from PostgreSQL. */
export interface IngestPayload {
  videoId: string;
  pipelineVersion: string;
  processingRun: number;
  correlationId?: string;
}

export interface TranscriptionPayload {
  videoId: string;
  transcriptVersion: number;
  processingRun: number;
  correlationId?: string;
}

export interface AnalysisPayload {
  videoId: string;
  transcriptId: string;
  analysisVersion: string;
  analysisRun: number;
  correlationId?: string;
}

export interface RenderPayload {
  renderId: string;
  videoId: string;
  settingsVersion: 1;
  attempt: number;
  correlationId?: string;
}

export interface CleanupPayload {
  kind: 'purge-video' | 'janitor';
  videoId?: string;
  userId?: string;
  correlationId?: string;
}

/**
 * The API's side of the BullMQ contract: one Queue per stage, every job carrying
 * a deterministic ID so a repeated call can never enqueue the same work twice.
 */
@Injectable()
export class QueueService implements OnModuleDestroy {
  private readonly logger = new Logger(QueueService.name);
  private readonly queues = new Map<QueueKey, Queue>();
  private connection?: IORedis;

  constructor() {
    if (config.openapiOnly) return;
    const connection = new IORedis(redisOptionsFromUrl(config.redisUrl));
    connection.on('error', (err) => { this.logger.warn({ err }, 'Redis connection error'); });
    this.connection = connection;
    for (const key of Object.keys(QUEUES) as QueueKey[]) {
      const q = new Queue(QUEUES[key], { connection, prefix: config.queuePrefix, defaultJobOptions: JOB_DEFAULTS[key] });
      q.on('error', (err) => { this.logger.error({ err, queue: QUEUES[key] }, 'Queue connection error'); });
      this.queues.set(key, q);
    }
  }

  private queue(key: QueueKey): Queue {
    const q = this.queues.get(key);
    if (!q) throw new Error(`Queue ${key} is not initialised`);
    return q;
  }

  async enqueueIngest(p: IngestPayload) {
    const jobId = jobIds.ingest(p.videoId, p.pipelineVersion, p.processingRun);
    await this.queue('ingest').add('ingest', p, { jobId });
    return jobId;
  }

  async enqueueTranscription(p: TranscriptionPayload) {
    const jobId = jobIds.transcription(p.videoId, p.transcriptVersion, p.processingRun);
    await this.queue('transcription').add('transcribe', p, { jobId });
    return jobId;
  }

  async enqueueAnalysis(p: AnalysisPayload) {
    const jobId = jobIds.analysis(p.videoId, p.analysisVersion, p.analysisRun);
    await this.queue('analysis').add('analyze', p, { jobId });
    return jobId;
  }

  async enqueueRender(p: RenderPayload) {
    const jobId = jobIds.render(p.renderId, p.attempt);
    await this.queue('render').add('render', p, { jobId });
    return jobId;
  }

  async enqueueCleanup(p: CleanupPayload) {
    const jobId = jobIds.cleanup(p.kind, p.videoId ?? 'all');
    await this.queue('cleanup').add(p.kind, p, { jobId });
    return jobId;
  }

  async counts(): Promise<Record<string, Record<string, number>>> {
    const out: Record<string, Record<string, number>> = {};
    for (const [key, q] of this.queues) {
      out[QUEUES[key]] = await q.getJobCounts('waiting', 'active', 'delayed', 'failed', 'prioritized');
    }
    return out;
  }

  async ping(): Promise<boolean> {
    try {
      return (await this.connection?.ping()) === 'PONG';
    } catch {
      return false;
    }
  }

  async onModuleDestroy() {
    await Promise.all([...this.queues.values()].map((q) => q.close()));
    await this.connection?.quit();
  }
}
