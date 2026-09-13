/**
 * Deterministic R2 object layout (PRD §16.1). Keys are always derived on the
 * server from owned IDs and are never accepted from client input.
 */

/** Container formats the pipeline accepts; anything else is rejected at upload. */
const ALLOWED_EXTENSIONS: readonly string[] = ['mp4', 'mov', 'webm', 'm4v', 'mkv'];

/** The file's extension when it is one we accept, otherwise null. */
export function fileExtension(filename: string): string | null {
  const dot = filename.lastIndexOf('.');
  if (dot <= 0) return null;
  const ext = filename.slice(dot + 1).toLowerCase();
  return ALLOWED_EXTENSIONS.includes(ext) ? ext : null;
}

/**
 * Reduces a user-supplied filename to something safe to embed in an object key:
 * no directory parts, no combining marks, a bounded stem and a known extension.
 */
export function sanitizeFilename(input: string): string {
  const base = (input || 'video').split(/[\\/]/).pop() || 'video';
  const normalized = base.normalize('NFKD').replace(/\p{M}/gu, '');
  const dot = normalized.lastIndexOf('.');
  const rawExt = dot > 0 ? normalized.slice(dot + 1).toLowerCase() : '';
  const stem = (dot > 0 ? normalized.slice(0, dot) : normalized)
    .replace(/[^A-Za-z0-9._-]+/g, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^[-.]+|[-.]+$/g, '')
    .slice(0, 80);
  const ext = ALLOWED_EXTENSIONS.includes(rawExt) ? rawExt : 'mp4';
  return `${stem || 'video'}.${ext}`;
}

export const objectKeys = {
  videoPrefix: (userId: string, videoId: string) => `users/${userId}/videos/${videoId}/`,
  source: (userId: string, videoId: string, originalName: string) =>
    `users/${userId}/videos/${videoId}/source/${sanitizeFilename(originalName)}`,
  audio: (userId: string, videoId: string, ext = 'mp3') => `users/${userId}/videos/${videoId}/derived/audio.${ext}`,
  thumbnail: (userId: string, videoId: string) => `users/${userId}/videos/${videoId}/derived/thumb.jpg`,
  proxy: (userId: string, videoId: string) => `users/${userId}/videos/${videoId}/derived/proxy.mp4`,
  renderOutput: (userId: string, videoId: string, renderId: string) =>
    `users/${userId}/videos/${videoId}/renders/${renderId}/final.mp4`,
  renderThumb: (userId: string, videoId: string, renderId: string) =>
    `users/${userId}/videos/${videoId}/renders/${renderId}/thumb.jpg`,
};
