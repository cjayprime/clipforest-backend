import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { randomUUID } from 'node:crypto';
import { DataSource, In, IsNull, Repository } from 'typeorm';
import { config } from '../config';
import { AuthUser } from '../common/decorators';
import { AppError, Errors } from '../common/errors';
import { planMultipart } from '../common/multipart';
import { fileExtension, objectKeys } from '../common/object-keys';
import { serializeCandidate, serializeVideo } from '../common/serializers';
import { normalizeSourceUrl } from '../common/source-url';
import { VIDEO_PROCESSING_STATES, VideoStatus, videoSourcesFor } from '../common/state-machine';
import { EventsService } from '../core/events.service';
import { MetricsService } from '../core/metrics.service';
import { QueueService } from '../core/queue.service';
import { StorageService } from '../core/storage.service';
import { Candidate, Render, Transcript, TranscriptSegmentJson, TranscriptWordJson, Video } from '../entities';
import { CandidatesQueryDto, CreateVideoDto, ListVideosQueryDto, TranscriptQueryDto, UploadCompleteDto } from './videos.dto';

const ACCEPTED_TYPES = [
  'video/mp4',
  'video/quicktime',
  'video/webm',
  'video/x-matroska',
  'video/x-m4v',
  'application/octet-stream',
  '',
];

@Injectable()
export class VideosService {
  private readonly logger = new Logger(VideosService.name);

  constructor(
    @InjectRepository(Video) private readonly videos: Repository<Video>,
    @InjectRepository(Candidate) private readonly candidatesRepo: Repository<Candidate>,
    @InjectRepository(Render) private readonly renders: Repository<Render>,
    @InjectRepository(Transcript) private readonly transcripts: Repository<Transcript>,
    private readonly dataSource: DataSource,
    private readonly storage: StorageService,
    private readonly queues: QueueService,
    private readonly events: EventsService,
    private readonly metrics: MetricsService,
  ) {}

  async getOwned(userId: string, id: string): Promise<Video> {
    const v = await this.videos.findOne({ where: { id, userId, deletedAt: IsNull() } });
    if (!v) throw Errors.notFound('Video');
    return v;
  }

  private publish(v: Video) {
    return this.events.publish({
      type: 'video.updated',
      userId: v.userId,
      videoId: v.id,
      status: v.status,
      progress: v.progress,
      stage: v.stage,
      substage: v.substage,
      errorCode: v.errorCode,
    });
  }

  /** Conditional status update: only succeeds if the row is still in an allowed source state. */
  private async transition(
    id: string,
    to: VideoStatus,
    data: Record<string, unknown>,
    from?: VideoStatus[],
  ): Promise<boolean> {
    const res = await this.videos
      .createQueryBuilder()
      .update(Video)
      .set({ ...data, status: to })
      .where('id = :id AND deleted_at IS NULL AND status IN (:...from)', { id, from: from ?? videoSourcesFor(to) })
      .execute();
    return res.affected === 1;
  }

  async list(userId: string, q: ListVideosQueryDto) {
    const statusFilter: Record<string, string[] | undefined> = {
      all: undefined,
      processing: ['CREATED', 'UPLOADING', ...VIDEO_PROCESSING_STATES],
      ready: ['READY'],
      failed: ['FAILED'],
    };
    const statuses = statusFilter[q.filter ?? 'all'];
    const videos = await this.videos.find({
      where: { userId, deletedAt: IsNull(), ...(statuses ? { status: In(statuses) } : {}) },
      order: { createdAt: 'DESC' },
      take: 200,
    });
    const ids = videos.map((v) => v.id);
    const [candidateStats, renderStats] = ids.length
      ? await Promise.all([
          this.candidatesRepo
            .createQueryBuilder('c')
            .select('c.video_id', 'videoId')
            .addSelect('MAX(c.score)', 'topScore')
            .addSelect('COUNT(*)', 'count')
            .where('c.video_id IN (:...ids) AND c.superseded_at IS NULL', { ids })
            .groupBy('c.video_id')
            .getRawMany<{ videoId: string; topScore: string; count: string }>(),
          this.renders
            .createQueryBuilder('r')
            .select('r.video_id', 'videoId')
            .addSelect('COUNT(*)', 'count')
            .where('r.video_id IN (:...ids) AND r.deleted_at IS NULL', { ids })
            .groupBy('r.video_id')
            .getRawMany<{ videoId: string; count: string }>(),
        ])
      : [[], []];
    const candidatesById = new Map(candidateStats.map((s) => [s.videoId, s]));
    const rendersById = new Map(renderStats.map((s) => [s.videoId, Number(s.count)]));
    return {
      items: await Promise.all(
        videos.map(async (v) => {
          const s = candidatesById.get(v.id);
          return serializeVideo(v, {
            thumbnailUrl: v.thumbnailKey ? await this.storage.presignGet(v.thumbnailKey) : null,
            candidateCount: s ? Number(s.count) : 0,
            topScore: s ? Number(s.topScore) : null,
            renderCount: rendersById.get(v.id) ?? 0,
          });
        }),
      ),
    };
  }

  async create(user: AuthUser, dto: CreateVideoDto, correlationId: string) {
    if (dto.rightsConfirmed !== true) throw Errors.rightsRequired();
    const id = randomUUID();
    const base = { id, userId: user.id, pipelineVersion: config.pipelineVersion, rightsConfirmedAt: new Date() };

    if (dto.sourceType === 'url') {
      const src = normalizeSourceUrl(dto.sourceUrl ?? '', config.allowedSourceHosts);
      const video = await this.videos.save(
        this.videos.create({
          ...base,
          sourceType: 'url',
          sourceUrl: src.canonicalUrl,
          sourceProvider: src.provider,
          originalFilename: `${src.provider}-${src.externalId}.mp4`,
          title: dto.title?.trim() || `YouTube video ${src.externalId}`,
          status: 'QUEUED',
          processingRun: 1,
          stage: 'queued',
        }),
      );
      await this.queues.enqueueIngest({
        videoId: id,
        pipelineVersion: config.pipelineVersion,
        processingRun: 1,
        correlationId,
      });
      this.metrics.videosCreated.inc({ source_type: 'url' });
      await this.publish(video);
      return serializeVideo(video);
    }

    const filename = dto.originalFilename ?? '';
    if (!fileExtension(filename)) {
      throw Errors.unsupportedFile('Supported formats are MP4, MOV, WebM, M4V and MKV.');
    }
    const contentType = (dto.contentType ?? '').toLowerCase();
    if (!ACCEPTED_TYPES.includes(contentType)) {
      throw Errors.unsupportedFile(`Files of type "${contentType}" are not supported.`);
    }
    if ((dto.sizeBytes ?? 0) > config.maxUploadBytes) throw Errors.uploadTooLarge(config.maxUploadBytes);

    const video = await this.videos.save(
      this.videos.create({
        ...base,
        sourceType: 'upload',
        sourceProvider: 'upload',
        originalFilename: filename.slice(0, 255),
        title: dto.title?.trim() || filename.replace(/\.[^.]+$/, '').slice(0, 200) || 'Untitled video',
        contentType: contentType || 'application/octet-stream',
        sizeBytes: dto.sizeBytes ?? 0,
        objectKey: objectKeys.source(user.id, id, filename),
        status: 'CREATED',
      }),
    );
    this.metrics.videosCreated.inc({ source_type: 'upload' });
    return serializeVideo(video);
  }

  async createUploadSession(userId: string, id: string) {
    const v = await this.getOwned(userId, id);
    if (v.sourceType !== 'upload' || !v.objectKey) throw Errors.invalidState('This video was not created for direct upload.');
    if (v.status !== 'CREATED' && v.status !== 'UPLOADING') {
      throw Errors.invalidState('The upload for this video has already been completed.');
    }
    const size = Number(v.sizeBytes ?? 0);
    const contentType = v.contentType || 'application/octet-stream';

    if (size < config.multipartThresholdBytes) {
      await this.transition(id, 'UPLOADING', { uploadId: null, stage: 'uploading', progress: 0 }, ['CREATED', 'UPLOADING']);
      return {
        mode: 'single' as const,
        url: await this.storage.presignPut(v.objectKey, contentType),
        headers: { 'Content-Type': contentType },
        expiresInSec: config.uploadUrlTtlSec,
      };
    }

    // A refreshed page starts a fresh multipart session; the stale one is aborted.
    if (v.uploadId) await this.storage.abortMultipart(v.objectKey, v.uploadId);
    const uploadId = await this.storage.createMultipart(v.objectKey, contentType);
    await this.transition(id, 'UPLOADING', { uploadId, stage: 'uploading', progress: 0 }, ['CREATED', 'UPLOADING']);
    const { partSize, partCount } = planMultipart(size, config.multipartPartSizeBytes);
    const first = Array.from({ length: Math.min(partCount, config.partUrlBatchSize) }, (_, i) => i + 1);
    return {
      mode: 'multipart' as const,
      uploadId,
      partSize,
      partCount,
      parts: await Promise.all(
        first.map(async (n) => ({ partNumber: n, url: await this.storage.presignPart(v.objectKey!, uploadId, n) })),
      ),
      expiresInSec: config.uploadUrlTtlSec,
    };
  }

  async signParts(userId: string, id: string, partNumbers: number[]) {
    const v = await this.getOwned(userId, id);
    if (v.status !== 'UPLOADING' || !v.uploadId || !v.objectKey) throw Errors.invalidState('No multipart upload is in progress.');
    const { partCount } = planMultipart(Number(v.sizeBytes ?? 0), config.multipartPartSizeBytes);
    const unique = [...new Set(partNumbers)];
    if (unique.some((n) => n > partCount)) throw Errors.invalidState(`Part numbers must be between 1 and ${partCount}.`);
    return {
      parts: await Promise.all(
        unique.map(async (n) => ({ partNumber: n, url: await this.storage.presignPart(v.objectKey!, v.uploadId!, n) })),
      ),
      expiresInSec: config.uploadUrlTtlSec,
    };
  }

  /**
   * Idempotent: the conditional status transition means only the first caller
   * enqueues ingestion; retried callbacks and refreshes return the current state.
   */
  async completeUpload(userId: string, id: string, dto: UploadCompleteDto, correlationId: string) {
    const v = await this.getOwned(userId, id);
    if (v.status !== 'CREATED' && v.status !== 'UPLOADING') {
      if (v.status === 'FAILED') throw Errors.invalidState('This upload failed. Create a new video to try again.');
      return serializeVideo(v);
    }
    if (!v.objectKey) throw Errors.invalidState('This video was not created for direct upload.');

    if (v.uploadId) {
      if (!dto.parts?.length) throw Errors.invalidState('Multipart uploads must report their completed parts.');
      await this.storage.completeMultipart(v.objectKey, v.uploadId, dto.parts);
    }
    const head = await this.storage.head(v.objectKey);
    if (!head) throw Errors.uploadObjectMissing();
    if (head.size > config.maxUploadBytes) {
      await this.transition(id, 'FAILED', {
        errorCode: 'UPLOAD_TOO_LARGE',
        errorMessage: 'The uploaded file exceeds the maximum size.',
        errorRetryable: false,
        errorCorrelationId: correlationId,
        failedStage: 'UPLOADING',
      });
      throw Errors.uploadTooLarge(config.maxUploadBytes);
    }

    const won = await this.transition(
      id,
      'QUEUED',
      {
        sizeBytes: head.size,
        uploadId: null,
        uploadCompletedAt: new Date(),
        processingRun: () => '"processing_run" + 1',
        progress: 0,
        stage: 'queued',
        substage: null,
      },
      ['CREATED', 'UPLOADING'],
    );
    const current = await this.getOwned(userId, id);
    if (won) {
      await this.queues.enqueueIngest({
        videoId: id,
        pipelineVersion: current.pipelineVersion,
        processingRun: current.processingRun,
        correlationId,
      });
      await this.publish(current);
    }
    return serializeVideo(current);
  }

  /** Start/restart processing through the validated retry path (PRD §29.1). */
  async process(userId: string, id: string, correlationId: string) {
    const v = await this.getOwned(userId, id);
    if ((VIDEO_PROCESSING_STATES as readonly string[]).includes(v.status)) return serializeVideo(v);
    if (v.status === 'READY') throw Errors.invalidState('This video is already processed. Use re-analyze to find new moments.');
    if (v.status === 'CREATED' || v.status === 'UPLOADING') throw Errors.invalidState('Finish uploading before processing.');
    if (v.errorRetryable === false) {
      throw Errors.notRetryable(v.errorMessage ?? 'This video cannot be processed. Try a different file.');
    }
    if (v.sourceType === 'upload' && !v.uploadCompletedAt) throw Errors.invalidState('The upload never completed.');

    const clearError = { errorCode: null, errorMessage: null, errorRetryable: null, errorCorrelationId: null, substage: null };
    let resumed: 'analysis' | 'transcription' | 'ingest';
    if (v.activeTranscriptId) {
      resumed = 'analysis';
      if (!(await this.transition(id, 'ANALYZING', { ...clearError, analysisRun: () => '"analysis_run" + 1', progress: 50, stage: 'analysis' }, ['FAILED']))) {
        return serializeVideo(await this.getOwned(userId, id));
      }
    } else if (v.failedStage === 'TRANSCRIBING' && v.audioKey) {
      resumed = 'transcription';
      if (!(await this.transition(id, 'TRANSCRIBING', { ...clearError, processingRun: () => '"processing_run" + 1', progress: 15, stage: 'transcription' }, ['FAILED']))) {
        return serializeVideo(await this.getOwned(userId, id));
      }
    } else {
      resumed = 'ingest';
      if (!(await this.transition(id, 'QUEUED', { ...clearError, processingRun: () => '"processing_run" + 1', progress: 0, stage: 'queued' }, ['FAILED']))) {
        return serializeVideo(await this.getOwned(userId, id));
      }
    }
    const cur = await this.getOwned(userId, id);
    if (resumed === 'analysis') {
      await this.queues.enqueueAnalysis({
        videoId: id,
        transcriptId: cur.activeTranscriptId!,
        analysisVersion: config.analysisVersion,
        analysisRun: cur.analysisRun,
        correlationId,
      });
    } else if (resumed === 'transcription') {
      const latest = await this.transcripts.findOne({ where: { videoId: id }, order: { version: 'DESC' } });
      await this.queues.enqueueTranscription({
        videoId: id,
        transcriptVersion: latest?.status === 'COMPLETED' ? latest.version + 1 : (latest?.version ?? 1),
        processingRun: cur.processingRun,
        correlationId,
      });
    } else {
      await this.queues.enqueueIngest({
        videoId: id,
        pipelineVersion: cur.pipelineVersion,
        processingRun: cur.processingRun,
        correlationId,
      });
    }
    await this.publish(cur);
    return serializeVideo(cur);
  }

  /** Re-run candidate discovery using the persisted transcript (no re-transcription). */
  async analyze(userId: string, id: string, correlationId: string) {
    const v = await this.getOwned(userId, id);
    if (v.status === 'ANALYZING') return serializeVideo(v);
    if (!v.activeTranscriptId) throw Errors.invalidState('This video has no transcript yet.');
    if (v.status !== 'READY' && v.status !== 'FAILED') throw Errors.invalidState('Wait for processing to finish first.');
    const ok = await this.transition(
      id,
      'ANALYZING',
      {
        analysisRun: () => '"analysis_run" + 1',
        progress: 50,
        stage: 'analysis',
        substage: 'queued',
        errorCode: null,
        errorMessage: null,
        errorRetryable: null,
        errorCorrelationId: null,
      },
      ['READY', 'FAILED'],
    );
    const cur = await this.getOwned(userId, id);
    if (ok) {
      await this.queues.enqueueAnalysis({
        videoId: id,
        transcriptId: cur.activeTranscriptId!,
        analysisVersion: config.analysisVersion,
        analysisRun: cur.analysisRun,
        correlationId,
      });
      await this.publish(cur);
    }
    return serializeVideo(cur);
  }

  async get(userId: string, id: string) {
    const v = await this.getOwned(userId, id);
    return serializeVideo(v, { thumbnailUrl: v.thumbnailKey ? await this.storage.presignGet(v.thumbnailKey) : null });
  }

  /** Short-lived playback URLs for the source (or a browser-compatible proxy when one was generated). */
  async playback(userId: string, id: string) {
    const v = await this.getOwned(userId, id);
    const key = v.proxyKey ?? v.objectKey;
    const ready = Boolean(key) && Boolean(v.uploadCompletedAt || v.sourceType === 'url') && !v.sourceExpiredAt;
    return {
      sourceUrl: ready && key ? await this.storage.presignGet(key) : null,
      isProxy: Boolean(v.proxyKey),
      thumbnailUrl: v.thumbnailKey ? await this.storage.presignGet(v.thumbnailKey) : null,
      expiresAt: new Date(Date.now() + config.playbackUrlTtlSec * 1000).toISOString(),
    };
  }

  async candidates(userId: string, id: string, q: CandidatesQueryDto) {
    const v = await this.getOwned(userId, id);
    const all = await this.candidatesRepo.find({
      where: { videoId: v.id, supersededAt: IsNull() },
      order: q.sort === 'time' ? { startMs: 'ASC' } : { score: 'DESC', rank: 'ASC' },
    });
    const minScore = q.minScore ?? config.minCandidateScore;
    const visible = all.filter((c) => c.score >= minScore);
    const renders = visible.length
      ? await this.renders.find({
          where: { candidateId: In(visible.map((c) => c.id)), isLatest: true, deletedAt: IsNull() },
          select: { id: true, candidateId: true, status: true, progress: true, version: true },
        })
      : [];
    const renderByCandidate = new Map(renders.map((r) => [r.candidateId, r]));
    return {
      videoStatus: v.status,
      analysisRun: v.analysisRun,
      minScore,
      defaultMinScore: config.minCandidateScore,
      total: all.length,
      hiddenCount: all.length - visible.length,
      items: visible.map((c) => serializeCandidate(c, { latestRender: renderByCandidate.get(c.id) ?? null })),
    };
  }

  async transcript(userId: string, id: string, q: TranscriptQueryDto) {
    const v = await this.getOwned(userId, id);
    if (!v.activeTranscriptId) throw Errors.notFound('Transcript');
    if (q.endMs <= q.startMs || q.endMs - q.startMs > 30 * 60 * 1000) {
      throw new AppError('VALIDATION_FAILED', 'Transcript ranges must be positive and at most 30 minutes.', 400);
    }
    const t = await this.transcripts.findOne({ where: { id: v.activeTranscriptId } });
    if (!t) throw Errors.notFound('Transcript');
    const overlaps = (x: TranscriptWordJson) => x.endMs > q.startMs && x.startMs < q.endMs;
    return {
      language: t.language,
      provider: t.provider,
      startMs: q.startMs,
      endMs: q.endMs,
      segments: (t.segments as TranscriptSegmentJson[]).filter(overlaps),
      words: (t.words as TranscriptWordJson[]).filter(overlaps),
    };
  }

  /** Soft-delete now; storage objects and rows are purged by the cleanup queue. */
  async remove(userId: string, id: string, correlationId: string) {
    const v = await this.getOwned(userId, id);
    const now = new Date();
    await this.dataSource.transaction(async (m) => {
      await m.update(Video, { id }, { deletedAt: now });
      await m.update(Render, { videoId: id, deletedAt: IsNull() }, { deletedAt: now });
      await m.update(Candidate, { videoId: id, supersededAt: IsNull() }, { supersededAt: now });
    });
    if (v.uploadId && v.objectKey) await this.storage.abortMultipart(v.objectKey, v.uploadId);
    try {
      await this.queues.enqueueCleanup({ kind: 'purge-video', videoId: id, userId, correlationId });
    } catch (err) {
      // The worker janitor re-schedules purges for soft-deleted videos, so this is recoverable.
      this.logger.warn({ err, videoId: id }, 'Could not enqueue purge; janitor will retry');
    }
    await this.events.publish({ type: 'video.updated', userId, videoId: id, status: 'DELETED' });
    return { id, deleted: true, storageCleanup: 'scheduled' };
  }
}
