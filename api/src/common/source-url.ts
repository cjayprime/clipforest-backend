import { Errors } from './errors';

/**
 * URL ingestion input validation (PRD §5 FR-ING-002, §18 SSRF). Source URLs are
 * untrusted: only allow-listed hosts are accepted and the URL is rebuilt from
 * the parsed video ID, so nothing user-controlled other than the ID reaches the
 * downloader.
 */
const YT_ID = /^[A-Za-z0-9_-]{11}$/;

export interface NormalizedSource {
  provider: 'youtube';
  externalId: string;
  canonicalUrl: string;
}

export function normalizeSourceUrl(raw: string, allowedHosts: readonly string[]): NormalizedSource {
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    throw Errors.unsupportedSource('That does not look like a valid URL.');
  }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') {
    throw Errors.unsupportedSource('Only http(s) links are supported.');
  }
  if (url.username || url.password || url.port) {
    throw Errors.unsupportedSource('Links with credentials or custom ports are not supported.');
  }
  const host = url.hostname.toLowerCase();
  if (!allowedHosts.includes(host)) {
    throw Errors.unsupportedSource('Only YouTube links are supported right now. Upload the file directly instead.');
  }

  let id: string | null = null;
  if (host === 'youtu.be') {
    id = url.pathname.split('/').filter(Boolean)[0] ?? null;
  } else if (url.pathname === '/watch') {
    id = url.searchParams.get('v');
  } else {
    const m = url.pathname.match(/^\/(?:shorts|live|embed)\/([^/?#]+)/);
    id = m?.[1] ?? null;
  }
  if (!id || !YT_ID.test(id)) {
    throw Errors.unsupportedSource('Could not find a YouTube video ID in that link. Playlists and channels are not supported.');
  }
  return { provider: 'youtube', externalId: id, canonicalUrl: `https://www.youtube.com/watch?v=${id}` };
}
