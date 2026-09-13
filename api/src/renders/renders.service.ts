import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { randomUUID } from 'node:crypto';
import { DataSource, In, IsNull, Repository } from 'typeorm';
import { config } from '../config';
import { BillingService } from '../billing/billing.service';
import { AuthUser } from '../common/decorators';
import { Errors, isUniqueViolation } from '../common/errors';
import { renderSettingsHash } from '../common/idempotency';
import { buildRenderSettings, RenderSettings, validateRenderRange } from '../common/render-settings';
import { serializeRender } from '../common/serializers';
import { RENDER_ACTIVE_STATES } from '../common/state-machine';
import { EventsService } from '../core/events.service';
import { MetricsService } from '../core/metrics.service';
import { QueueService } from '../core/queue.service';
import { StorageService } from '../core/storage.service';
import { Candidate, Render, RenderSettingsJson, Video } from '../entities';
import { CreateRenderDto, ManualRenderDto } from './renders.dto';

interface CreateRenderInput {
  video: Video;
  userId: string;
  candidateId: string | null;
  parent: Render | null;
  lineageKey: string;
  startMs: number;
  endMs: number;
  settings: RenderSettings;
  title: string;
  force: boolean;
  correlationId: string;
}

@Injectable()
export class RendersService {
  private readonly logger = new Logger(RendersService.name);

  constructor(
    @InjectRepository(Render) private readonly renders: Repository<Render>,
    @InjectRepository(Video) private readonly videos: Repository<Video>,
    @InjectRepository(Candidate) private readonly candidates: Repository<Candidate>,
    private readonly dataSource: DataSource,
    private readonly storage: StorageService,
    private readonly queues: QueueService,
    private readonly events: EventsService,
    private readonly metrics: MetricsService,
    private readonly billing: BillingService,
  ) {}

  private async ownedVideo(userId: string, videoId: string) {
    const v = await this.videos.findOne({ where: { video_id: videoId, user_id: userId, deletedAt: IsNull() } });
    if (!v) throw Errors.notFound('Video');
    return v;
  }

  private async ownedRender(userId: string, id: string) {
    const r = await this.renders.findOne({ where: { render_id: id, user_id: userId, deletedAt: IsNull() } });
    if (!r) throw Errors.notFound('Clip');
    return r;
  }

  private assertRenderable(v: Video) {
    if (!v.durationMs || !v.active_transcript_id) throw Errors.invalidState('This video has not finished processing yet.');
    if (v.sourceExpiredAt || !v.objectKey) throw Errors.invalidState('The source video is no longer available for rendering.');
  }

  private publish(r: Render) {
    return this.events.publish({
      type: 'render.updated',
      userId: r.user_id,
      videoId: r.video_id,
      renderId: r.render_id,
      status: r.status,
      progress: r.progress,
      stage: r.stage,
      substage: r.substage,
      errorCode: r.errorCode,
    });
  }

  async fromCandidate(user: AuthUser, candidateId: string, dto: CreateRenderDto, correlationId: string) {
    const candidate = await this.candidates.findOne({ where: { candidate_id: candidateId } });
    if (!candidate) throw Errors.notFound('Candidate');
    const video = await this.ownedVideo(user.id, candidate.video_id);
    this.assertRenderable(video);
    return this.create({
      video,
      userId: user.id,
      candidateId: candidate.candidate_id,
      parent: null,
      lineageKey: candidate.candidate_id,
      startMs: dto.startMs ?? candidate.startMs,
      endMs: dto.endMs ?? candidate.endMs,
      settings: buildRenderSettings(dto),
      title: dto.title?.trim() || candidate.title,
      force: dto.force ?? false,
      correlationId,
    });
  }

  /** Manual range (e.g. from the no-candidate state) or a continuation of an existing lineage. */
  async manual(user: AuthUser, videoId: string, dto: ManualRenderDto, correlationId: string) {
    const video = await this.ownedVideo(user.id, videoId);
    this.assertRenderable(video);
    const parent = dto.parentRenderId ? await this.ownedRender(user.id, dto.parentRenderId) : null;
    if (parent && parent.video_id !== video.video_id) throw Errors.invalidState('That clip belongs to a different video.');
    if (!parent && (dto.startMs === undefined || dto.endMs === undefined)) {
      throw Errors.invalidRange('Choose a start and end time for the clip.');
    }
    return this.create({
      video,
      userId: user.id,
      candidateId: parent?.candidate_id ?? null,
      parent,
      lineageKey: parent?.lineageKey ?? randomUUID(),
      startMs: dto.startMs ?? parent!.startMs,
      endMs: dto.endMs ?? parent!.endMs,
      settings: buildRenderSettings(dto, parent ? (parent.settings as unknown as RenderSettings) : undefined),
      title: dto.title?.trim() || parent?.title || 'Custom clip',
      force: dto.force ?? false,
      correlationId,
    });
  }

  /** Adjust + rerender: a new version in the same lineage; the completed parent stays immutable. */
  async rerender(user: AuthUser, renderId: string, dto: CreateRenderDto, correlationId: string) {
    const parent = await this.ownedRender(user.id, renderId);
    const video = await this.ownedVideo(user.id, parent.video_id);
    this.assertRenderable(video);
    return this.create({
      video,
      userId: user.id,
      candidateId: parent.candidate_id,
      parent,
      lineageKey: parent.lineageKey,
      startMs: dto.startMs ?? parent.startMs,
      endMs: dto.endMs ?? parent.endMs,
      settings: buildRenderSettings(dto, parent.settings as unknown as RenderSettings),
      title: dto.title?.trim() || parent.title,
      force: dto.force ?? false,
      correlationId,
    });
  }

  private async create(input: CreateRenderInput) {
    const { video } = input;
    validateRenderRange(input.startMs, input.endMs, video.durationMs!, config.renderMinDurationMs, config.renderMaxDurationMs);
    const settingsHash = renderSettingsHash({
      videoId: video.video_id,
      startMs: input.startMs,
      endMs: input.endMs,
      settings: input.settings,
      pipelineVersion: config.pipelineVersion,
    });

    // An identical snapshot that is still processing (or already done) is returned instead of duplicated.
    const existing = await this.renders.findOne({
      where: {
        user_id: input.userId,
        settingsHash,
        deletedAt: IsNull(),
        status: In(input.force ? [...RENDER_ACTIVE_STATES] : [...RENDER_ACTIVE_STATES, 'COMPLETED']),
      },
      order: { createdAt: 'DESC' },
    });
    if (existing) {
      this.metrics.rendersDeduplicated.inc();
      return { ...(await this.present(existing)), deduplicated: true };
    }
    // After the dedupe check, so asking again for a clip that already exists is
    // free. The worker debits when the render job starts and refunds a failure.
    await this.billing.assertCanAfford(input.userId, config.credits.costPerRender);

    let render: Render;
    try {
      render = await this.dataSource.transaction(async (m) => {
        const last = await m.findOne(Render, { where: { lineageKey: input.lineageKey }, order: { version: 'DESC' } });
        await m.update(Render, { lineageKey: input.lineageKey, isLatest: true }, { isLatest: false });
        return m.save(
          m.create(Render, {
            video_id: video.video_id,
            user_id: input.userId,
            candidate_id: input.candidateId,
            parent_render_id: input.parent?.render_id ?? null,
            lineageKey: input.lineageKey,
            version: (last?.version ?? 0) + 1,
            isLatest: true,
            title: input.title.slice(0, 120),
            startMs: input.startMs,
            endMs: input.endMs,
            settings: input.settings as unknown as RenderSettingsJson,
            settingsHash,
            pipelineVersion: config.pipelineVersion,
            status: 'QUEUED',
            stage: 'queued',
          }),
        );
      });
    } catch (err) {
      // Concurrent identical request won the (lineage, version) slot: return what it created.
      if (isUniqueViolation(err)) {
        const winner = await this.renders.findOne({ where: { user_id: input.userId, settingsHash }, order: { createdAt: 'DESC' } });
        if (winner) return { ...(await this.present(winner)), deduplicated: true };
      }
      throw err;
    }

    await this.queues.enqueueRender({
      renderId: render.render_id,
      videoId: video.video_id,
      settingsVersion: 1,
      attempt: render.attempt,
      correlationId: input.correlationId,
    });
    this.metrics.rendersCreated.inc({ kind: render.version > 1 ? 'rerender' : 'new' });
    await this.publish(render);
    return { ...(await this.present(render)), deduplicated: false };
  }

  /** Serialize with fresh signed URLs (refreshed on every read, object identity unchanged). */
  private async present(r: Render, extra: Record<string, unknown> = {}) {
    const done = r.status === 'COMPLETED' && r.outputKey;
    const safeTitle = r.title.replace(/[^A-Za-z0-9 _-]+/g, '').trim().slice(0, 60) || 'clip';
    return serializeRender(r, {
      outputUrl: done ? await this.storage.presignGet(r.outputKey!) : null,
      downloadUrl: done ? await this.storage.presignGet(r.outputKey!, { downloadName: `${safeTitle}-v${r.version}.mp4` }) : null,
      thumbnailUrl: r.thumbnailKey ? await this.storage.presignGet(r.thumbnailKey) : null,
      urlsExpireAt: done ? new Date(Date.now() + config.playbackUrlTtlSec * 1000).toISOString() : null,
      ...extra,
    });
  }

  async get(userId: string, id: string) {
    const r = await this.ownedRender(userId, id);
    const [video, versions, candidate] = await Promise.all([
      this.videos.findOne({ where: { video_id: r.video_id } }),
      // Full rows: the version list renders each snapshot's range and caption preset.
      this.renders.find({ where: { lineageKey: r.lineageKey, deletedAt: IsNull() }, order: { version: 'DESC' } }),
      r.candidate_id ? this.candidates.findOne({ where: { candidate_id: r.candidate_id } }) : null,
    ]);
    return this.present(r, {
      video: video
        ? {
            id: video.video_id,
            title: video.title,
            durationMs: video.durationMs,
            width: video.width,
            height: video.height,
            status: video.status,
          }
        : null,
      candidate: candidate
        ? {
            id: candidate.candidate_id,
            title: candidate.title,
            score: candidate.score,
            startMs: candidate.startMs,
            endMs: candidate.endMs,
            reason: candidate.reason,
            category: candidate.category,
          }
        : null,
      versions: versions.map((v) => serializeRender(v)),
    });
  }

  async list(userId: string, videoId?: string) {
    const renders = await this.renders.find({
      where: { user_id: userId, deletedAt: IsNull(), ...(videoId ? { video_id: videoId } : {}), video: { deletedAt: IsNull() } },
      relations: { video: true },
      order: { createdAt: 'DESC' },
      take: 200,
    });
    return {
      items: await Promise.all(
        renders.map(async (r) =>
          serializeRender(r, {
            // The relation is loaded and filtered on above, so it is always present.
            videoTitle: r.video.title,
            thumbnailUrl: r.thumbnailKey ? await this.storage.presignGet(r.thumbnailKey) : null,
          }),
        ),
      ),
    };
  }

  /** Title is metadata only in P0 (not burned into the video). */
  async updateTitle(userId: string, id: string, title: string) {
    await this.ownedRender(userId, id);
    await this.renders.update({ render_id: id }, { title: title.trim() });
    return this.present(await this.ownedRender(userId, id));
  }

  /** Idempotent retry of a failed render; active renders are returned unchanged. */
  async retry(userId: string, id: string, correlationId: string) {
    const r = await this.ownedRender(userId, id);
    if ((RENDER_ACTIVE_STATES as readonly string[]).includes(r.status)) return this.present(r);
    if (r.status === 'COMPLETED') throw Errors.invalidState('Completed clips are immutable. Adjust settings to create a new version.');
    if (r.errorRetryable === false) throw Errors.notRetryable(r.errorMessage ?? 'This clip cannot be retried.');
    const video = await this.ownedVideo(userId, r.video_id);
    this.assertRenderable(video);
    // The failed attempt was refunded, so a retry is charged like a new render.
    await this.billing.assertCanAfford(userId, config.credits.costPerRender);

    const res = await this.renders
      .createQueryBuilder()
      .update(Render)
      .set({
        status: 'QUEUED',
        attempt: () => '"attempt" + 1',
        progress: 0,
        stage: 'queued',
        substage: null,
        errorCode: null,
        errorMessage: null,
        errorRetryable: null,
        errorCorrelationId: null,
      })
      .where('render_id = :id AND status = :status', { id, status: 'FAILED' })
      .execute();
    const cur = await this.ownedRender(userId, id);
    if (res.affected === 1) {
      await this.queues.enqueueRender({ renderId: id, videoId: cur.video_id, settingsVersion: 1, attempt: cur.attempt, correlationId });
      await this.publish(cur);
    }
    return this.present(cur);
  }

  async remove(userId: string, id: string) {
    const r = await this.ownedRender(userId, id);
    await this.renders.update({ render_id: id }, { deletedAt: new Date(), isLatest: false });
    // Promote the newest remaining version in the lineage to "latest".
    const next = await this.renders.findOne({ where: { lineageKey: r.lineageKey, deletedAt: IsNull() }, order: { version: 'DESC' } });
    if (next && r.isLatest) await this.renders.update({ render_id: next.render_id }, { isLatest: true });
    return { id, deleted: true };
  }
}
