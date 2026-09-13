/**
 * Video and render state machines (PRD §29). The status vocabularies are
 * enforced by SQL CHECK constraints, and the transition tables are shared with
 * the Python worker (see worker/src/<package>/states.py).
 */

export const VIDEO_STATUSES = [
  'CREATED',
  'UPLOADING',
  'QUEUED',
  'INGESTING',
  'TRANSCRIBING',
  'ANALYZING',
  'READY',
  'FAILED',
] as const;

export type VideoStatus = (typeof VIDEO_STATUSES)[number];

/** States in which work is in flight, so the UI keeps polling. */
export const VIDEO_PROCESSING_STATES: readonly VideoStatus[] = ['QUEUED', 'INGESTING', 'TRANSCRIBING', 'ANALYZING'];

const VIDEO_TRANSITIONS: Record<VideoStatus, readonly VideoStatus[]> = {
  CREATED: ['UPLOADING', 'QUEUED', 'FAILED'],
  UPLOADING: ['UPLOADING', 'QUEUED', 'FAILED'],
  QUEUED: ['INGESTING', 'FAILED'],
  INGESTING: ['TRANSCRIBING', 'FAILED'],
  TRANSCRIBING: ['ANALYZING', 'FAILED'],
  ANALYZING: ['READY', 'FAILED'],
  // Explicit re-analysis only.
  READY: ['ANALYZING'],
  // Validated retry path only.
  FAILED: ['QUEUED', 'TRANSCRIBING', 'ANALYZING'],
};

export function canTransitionVideo(from: VideoStatus, to: VideoStatus): boolean {
  return VIDEO_TRANSITIONS[from].includes(to);
}

/** All statuses from which a transition to `to` is legal (used for atomic conditional updates). */
export function videoSourcesFor(to: VideoStatus): VideoStatus[] {
  return VIDEO_STATUSES.filter((s) => canTransitionVideo(s, to));
}

export const RENDER_STATUSES = ['QUEUED', 'PREPARING', 'ANALYZING_VISUALS', 'RENDERING', 'UPLOADING', 'COMPLETED', 'FAILED'] as const;

export type RenderStatus = (typeof RENDER_STATUSES)[number];

/** States in which a render is still working, so the UI keeps polling. */
export const RENDER_ACTIVE_STATES: readonly RenderStatus[] = ['QUEUED', 'PREPARING', 'ANALYZING_VISUALS', 'RENDERING', 'UPLOADING'];

const RENDER_TRANSITIONS: Record<RenderStatus, readonly RenderStatus[]> = {
  QUEUED: ['PREPARING', 'FAILED'],
  PREPARING: ['ANALYZING_VISUALS', 'RENDERING', 'FAILED'],
  ANALYZING_VISUALS: ['RENDERING', 'FAILED'],
  RENDERING: ['UPLOADING', 'FAILED'],
  UPLOADING: ['COMPLETED', 'FAILED'],
  // COMPLETED is immutable; a settings change creates a new render version.
  COMPLETED: [],
  FAILED: ['QUEUED'],
};

export function canTransitionRender(from: RenderStatus, to: RenderStatus): boolean {
  return RENDER_TRANSITIONS[from].includes(to);
}

export function renderSourcesFor(to: RenderStatus): RenderStatus[] {
  return RENDER_STATUSES.filter((s) => canTransitionRender(s, to));
}
