import { createHash } from 'node:crypto';

/** Canonical JSON: object keys sorted recursively so equal settings hash equally. */
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, v]) => v !== undefined)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${canonicalJson(v)}`).join(',')}}`;
}

/**
 * Identity of a render request. Two requests with the same source, range,
 * settings and pipeline version hash alike, which is how repeat "Generate"
 * clicks are answered by the existing render instead of encoding again.
 */
export function renderSettingsHash(input: {
  videoId: string;
  startMs: number;
  endMs: number;
  settings: unknown;
  pipelineVersion: string;
}): string {
  return createHash('sha256').update(canonicalJson(input)).digest('hex');
}

/**
 * Deterministic job IDs = idempotency keys (PRD §14.3): operation + entity +
 * version. BullMQ ignores an add() whose jobId already exists, so repeated API
 * calls (refresh, retried callbacks) can never enqueue the same work twice.
 * BullMQ forbids ':' in custom IDs, so '.' is the separator.
 */
export const jobIds = {
  ingest: (videoId: string, pipelineVersion: string, processingRun: number) =>
    `ingest.${videoId}.${pipelineVersion}.r${processingRun}`,
  transcription: (videoId: string, transcriptVersion: number, processingRun: number) =>
    `transcribe.${videoId}.v${transcriptVersion}.r${processingRun}`,
  analysis: (videoId: string, analysisVersion: string, analysisRun: number) =>
    `analyze.${videoId}.${analysisVersion}.a${analysisRun}`,
  render: (renderId: string, attempt: number) => `render.${renderId}.a${attempt}`,
  cleanup: (kind: string, entityId: string) => `cleanup.${kind}.${entityId}`,
};
