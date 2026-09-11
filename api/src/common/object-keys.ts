/**
 * Deterministic R2 object layout (PRD §16.1). Keys are always derived on the
 * server from owned IDs and are never accepted from client input.
 */

const ALLOWED_EXTENSIONS = ['mp4', 'mov', 'webm', 'm4v', 'mkv'] as const;

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
  const ext = (ALLOWED_EXTENSIONS as readonly string[]).includes(rawExt) ? rawExt : 'mp4';
  return `${stem || 'video'}.${ext}`;
}

export function fileExtension(filename: string): string | null {
  const dot = filename.lastIndexOf('.');
  if (dot <= 0) return null;
  const ext = filename.slice(dot + 1).toLowerCase();
  return (ALLOWED_EXTENSIONS as readonly string[]).includes(ext) ? ext : null;
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

/** Ownership check: an object key must live under the owning user's video prefix. */
export function isOwnedKey(key: string | null | undefined, userId: string, videoId: string): boolean {
  if (!key) return false;
  if (key.includes('..') || key.includes('//')) return false;
  return key.startsWith(objectKeys.videoPrefix(userId, videoId));
}
