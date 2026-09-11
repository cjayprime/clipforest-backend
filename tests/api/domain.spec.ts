import { canonicalJson, jobIds, renderSettingsHash } from '../../api/src/common/idempotency';
import { planMultipart } from '../../api/src/common/multipart';
import { isOwnedKey, objectKeys, sanitizeFilename } from '../../api/src/common/object-keys';
import { buildRenderSettings, DEFAULT_RENDER_SETTINGS, validateRenderRange } from '../../api/src/common/render-settings';
import { normalizeSourceUrl } from '../../api/src/common/source-url';
import { canTransitionRender, canTransitionVideo, renderSourcesFor, videoSourcesFor } from '../../api/src/common/state-machine';
import { AppError } from '../../api/src/common/errors';

const U = '11111111-1111-4111-8111-111111111111';
const V = '22222222-2222-4222-8222-222222222222';
const R = '33333333-3333-4333-8333-333333333333';

describe('state machines (PRD §29)', () => {
  it('follows the golden video path', () => {
    const path = ['CREATED', 'UPLOADING', 'QUEUED', 'INGESTING', 'TRANSCRIBING', 'ANALYZING', 'READY'] as const;
    for (let i = 0; i < path.length - 1; i++) expect(canTransitionVideo(path[i], path[i + 1])).toBe(true);
  });

  it('allows any processing state to fail, and retries only through validated states', () => {
    for (const s of ['QUEUED', 'INGESTING', 'TRANSCRIBING', 'ANALYZING'] as const) expect(canTransitionVideo(s, 'FAILED')).toBe(true);
    expect(canTransitionVideo('FAILED', 'QUEUED')).toBe(true);
    expect(canTransitionVideo('FAILED', 'TRANSCRIBING')).toBe(true);
    expect(canTransitionVideo('FAILED', 'ANALYZING')).toBe(true);
    expect(canTransitionVideo('FAILED', 'READY')).toBe(false);
    expect(canTransitionVideo('FAILED', 'INGESTING')).toBe(false);
  });

  it('only re-analyzes READY videos explicitly', () => {
    expect(canTransitionVideo('READY', 'ANALYZING')).toBe(true);
    expect(canTransitionVideo('READY', 'TRANSCRIBING')).toBe(false);
    expect(videoSourcesFor('ANALYZING').sort()).toEqual(['FAILED', 'READY', 'TRANSCRIBING'].sort());
  });

  it('treats COMPLETED renders as immutable', () => {
    expect(canTransitionRender('COMPLETED', 'QUEUED')).toBe(false);
    expect(canTransitionRender('COMPLETED', 'FAILED')).toBe(false);
    expect(canTransitionRender('FAILED', 'QUEUED')).toBe(true);
    expect(renderSourcesFor('COMPLETED')).toEqual(['UPLOADING']);
  });
});

describe('idempotency keys', () => {
  it('derives deterministic job ids from operation + entity + version', () => {
    expect(jobIds.ingest(V, '2026-09-mvp1', 1)).toBe(`ingest.${V}.2026-09-mvp1.r1`);
    expect(jobIds.render(R, 2)).toBe(`render.${R}.a2`);
    expect(jobIds.analysis(V, 'mvp1', 3)).toBe(`analyze.${V}.mvp1.a3`);
    for (const id of [jobIds.ingest(V, 'x', 1), jobIds.transcription(V, 1, 1), jobIds.cleanup('purge-video', V)]) {
      expect(id).not.toContain(':');
    }
  });

  it('hashes equal settings equally regardless of key order', () => {
    const a = renderSettingsHash({ videoId: V, startMs: 1, endMs: 2, settings: { a: 1, b: { c: 2, d: 3 } }, pipelineVersion: 'p' });
    const b = renderSettingsHash({ pipelineVersion: 'p', settings: { b: { d: 3, c: 2 }, a: 1 }, endMs: 2, startMs: 1, videoId: V });
    expect(a).toBe(b);
    expect(canonicalJson({ z: 1, a: [2, { y: 1, b: 2 }] })).toBe('{"a":[2,{"b":2,"y":1}],"z":1}');
    const c = renderSettingsHash({ videoId: V, startMs: 1, endMs: 3, settings: {}, pipelineVersion: 'p' });
    expect(c).not.toBe(a);
  });
});

describe('storage object keys', () => {
  it('builds the PRD layout under the owner prefix', () => {
    expect(objectKeys.source(U, V, 'My Podcast (ep 42).MP4')).toBe(`users/${U}/videos/${V}/source/My-Podcast-ep-42.mp4`);
    expect(objectKeys.renderOutput(U, V, R)).toBe(`users/${U}/videos/${V}/renders/${R}/final.mp4`);
    expect(objectKeys.renderThumb(U, V, R)).toBe(`users/${U}/videos/${V}/renders/${R}/thumb.jpg`);
  });

  it('sanitizes hostile filenames', () => {
    expect(sanitizeFilename('../../etc/passwd')).toBe('passwd.mp4');
    expect(sanitizeFilename('rm -rf $(whoami);.mov')).toBe('rm-rf-whoami.mov');
    expect(sanitizeFilename('Café déjà vu.webm')).toBe('Cafe-deja-vu.webm');
    expect(sanitizeFilename('')).toBe('video.mp4');
  });

  it('checks ownership', () => {
    expect(isOwnedKey(objectKeys.source(U, V, 'a.mp4'), U, V)).toBe(true);
    expect(isOwnedKey(`users/${U}/videos/${R}/source/a.mp4`, U, V)).toBe(false);
    expect(isOwnedKey(`users/${U}/videos/${V}/../${R}/a.mp4`, U, V)).toBe(false);
    expect(isOwnedKey(null, U, V)).toBe(false);
  });
});

describe('multipart planning', () => {
  it('keeps 16 MiB parts for normal files', () => {
    expect(planMultipart(100 * 1024 * 1024, 16 * 1024 * 1024)).toEqual({ partSize: 16 * 1024 * 1024, partCount: 7 });
  });

  it('never exceeds 10,000 parts for very large sources', () => {
    const size = 200 * 1024 ** 3;
    const plan = planMultipart(size, 16 * 1024 * 1024);
    expect(plan.partCount).toBeLessThanOrEqual(10_000);
    expect(plan.partSize % (1024 * 1024)).toBe(0);
  });

  it('handles the 4+ GB acceptance case', () => {
    const plan = planMultipart(4_318_827_341, 16 * 1024 * 1024);
    expect(plan.partCount).toBe(258);
  });
});

describe('source URL validation (SSRF defence)', () => {
  const hosts = ['youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'];
  it('canonicalizes supported YouTube links', () => {
    expect(normalizeSourceUrl('https://youtu.be/dQw4w9WgXcQ?t=10', hosts).canonicalUrl).toBe('https://www.youtube.com/watch?v=dQw4w9WgXcQ');
    expect(normalizeSourceUrl('https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=x', hosts).externalId).toBe('dQw4w9WgXcQ');
    expect(normalizeSourceUrl('https://www.youtube.com/shorts/dQw4w9WgXcQ', hosts).externalId).toBe('dQw4w9WgXcQ');
  });

  it.each([
    'http://169.254.169.254/latest/meta-data',
    'https://evil.com/watch?v=dQw4w9WgXcQ',
    'https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ',
    'file:///etc/passwd',
    'https://user:pass@youtube.com/watch?v=dQw4w9WgXcQ',
    'https://www.youtube.com:8443/watch?v=dQw4w9WgXcQ',
    'https://www.youtube.com/playlist?list=abc',
    'not a url',
  ])('rejects %s', (url) => {
    expect(() => normalizeSourceUrl(url, hosts)).toThrow(AppError);
  });
});

describe('render settings and range validation', () => {
  it('fills defaults and keeps parent settings on rerender', () => {
    expect(buildRenderSettings({})).toEqual(DEFAULT_RENDER_SETTINGS);
    const parent = buildRenderSettings({ framingMode: 'center', captions: { enabled: false, preset: 'karaoke' } });
    const next = buildRenderSettings({ captions: { enabled: true } }, parent);
    expect(next).toEqual({ ...parent, captions: { enabled: true, preset: 'karaoke' } });
  });

  it('rejects unsupported aspect ratios and modes', () => {
    expect(() => buildRenderSettings({ aspectRatio: '16:9' })).toThrow(AppError);
    expect(() => buildRenderSettings({ framingMode: 'zoom' })).toThrow(AppError);
  });

  it('enforces start < end, within duration, and min/max length', () => {
    const ok = () => validateRenderRange(1000, 31_000, 60_000, 5_000, 180_000);
    expect(ok).not.toThrow();
    expect(() => validateRenderRange(-1, 10_000, 60_000, 5_000, 180_000)).toThrow(AppError);
    expect(() => validateRenderRange(10_000, 10_000, 60_000, 5_000, 180_000)).toThrow(AppError);
    expect(() => validateRenderRange(10_000, 70_000, 60_000, 5_000, 180_000)).toThrow(AppError);
    expect(() => validateRenderRange(10_000, 12_000, 60_000, 5_000, 180_000)).toThrow(AppError);
    expect(() => validateRenderRange(0, 200_000, 400_000, 5_000, 180_000)).toThrow(AppError);
  });
});
