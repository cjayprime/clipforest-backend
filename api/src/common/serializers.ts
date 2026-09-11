import type { Candidate, Render, Video } from '../entities';

function errorOf(e: {
  errorCode: string | null;
  errorMessage: string | null;
  errorRetryable: boolean | null;
  errorCorrelationId: string | null;
}) {
  if (!e.errorCode) return null;
  return {
    code: e.errorCode,
    message: e.errorMessage ?? 'Processing failed.',
    retryable: e.errorRetryable ?? false,
    correlationId: e.errorCorrelationId,
  };
}

export function serializeVideo(v: Video, extra: Record<string, unknown> = {}) {
  return {
    id: v.id,
    title: v.title,
    originalFilename: v.originalFilename,
    sourceType: v.sourceType,
    sourceUrl: v.sourceUrl,
    sourceProvider: v.sourceProvider,
    contentType: v.contentType,
    sizeBytes: v.sizeBytes,
    durationMs: v.durationMs,
    width: v.width,
    height: v.height,
    fps: v.fps,
    orientation: v.orientation,
    hasAudio: v.hasAudio,
    videoCodec: v.videoCodec,
    audioCodec: v.audioCodec,
    language: v.language,
    status: v.status,
    progress: v.progress,
    stage: v.stage,
    substage: v.substage,
    failedStage: v.failedStage,
    pipelineVersion: v.pipelineVersion,
    error: errorOf(v),
    hasTranscript: Boolean(v.activeTranscriptId),
    sourceAvailable: Boolean(v.objectKey) && !v.sourceExpiredAt,
    createdAt: v.createdAt,
    updatedAt: v.updatedAt,
    uploadCompletedAt: v.uploadCompletedAt,
    readyAt: v.readyAt,
    ...extra,
  };
}

export function serializeCandidate(c: Candidate, extra: Record<string, unknown> = {}) {
  return {
    id: c.id,
    videoId: c.videoId,
    startMs: c.startMs,
    endMs: c.endMs,
    durationMs: c.endMs - c.startMs,
    title: c.title,
    hookText: c.hookText,
    excerpt: c.excerpt,
    summary: c.summary,
    reason: c.reason,
    category: c.category,
    score: c.score,
    componentScores: c.componentScores,
    rank: c.rank,
    analysisVersion: c.analysisVersion,
    provider: c.provider,
    createdAt: c.createdAt,
    ...extra,
  };
}

export function serializeRender(r: Render, extra: Record<string, unknown> = {}) {
  return {
    id: r.id,
    videoId: r.videoId,
    candidateId: r.candidateId,
    parentRenderId: r.parentRenderId,
    lineageKey: r.lineageKey,
    version: r.version,
    isLatest: r.isLatest,
    title: r.title,
    startMs: r.startMs,
    endMs: r.endMs,
    settings: r.settings,
    status: r.status,
    progress: r.progress,
    stage: r.stage,
    substage: r.substage,
    attempt: r.attempt,
    width: r.width,
    height: r.height,
    durationMs: r.durationMs,
    fileSize: r.fileSize,
    framing: r.framing,
    error: errorOf(r),
    createdAt: r.createdAt,
    startedAt: r.startedAt,
    completedAt: r.completedAt,
    ...extra,
  };
}
