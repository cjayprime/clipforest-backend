/**
 * Typed environment configuration. Every tunable lives here so the API and the
 * docs (.env.example) stay in sync. Secrets are only ever read from the
 * environment, never from source control.
 *
 * When the API runs natively (npm run start:dev) `dotenv` loads the backend
 * repo's single .env — the same file Compose is given via --env-file, resolved
 * from this module rather than the working directory. In containers the
 * environment comes from Docker Compose and no .env file exists.
 */
import { resolve } from 'node:path';
import { config as loadEnvFile } from 'dotenv';

// src/config.ts → backend/, and dist/config.js → backend/ too (nest build flattens src).
loadEnvFile({ path: resolve(__dirname, '..', '..', '.env'), quiet: true });

function str(name: string, fallback?: string): string {
  const v = process.env[name];
  if (v === undefined || v === '') {
    if (fallback === undefined) throw new Error(`Missing required environment variable ${name}`);
    return fallback;
  }
  return v;
}

function int(name: string, fallback: number): number {
  const v = process.env[name];
  if (v === undefined || v === '') return fallback;
  const n = Number(v);
  if (!Number.isFinite(n)) throw new Error(`Environment variable ${name} must be a number`);
  return Math.trunc(n);
}

function bool(name: string, fallback: boolean): boolean {
  const v = process.env[name];
  if (v === undefined || v === '') return fallback;
  return ['1', 'true', 'yes', 'on'].includes(v.toLowerCase());
}

function list(name: string, fallback: string[]): string[] {
  const v = process.env[name];
  if (!v) return fallback;
  return v.split(',').map((s) => s.trim()).filter(Boolean);
}

export function loadConfig() {
  const isProd = process.env.NODE_ENV === 'production';
  const openapiOnly = bool('OPENAPI_ONLY', false);
  return Object.freeze({
    isProd,
    openapiOnly,
    port: int('PORT', 4000),
    publicWebUrl: str('PUBLIC_WEB_URL', 'http://localhost:3000'),
    databaseUrl: str('DATABASE_URL', openapiOnly ? 'postgresql://x@localhost/x' : undefined),
    // Pending migrations are applied at boot; set false to run them separately.
    migrationsRun: bool('DB_MIGRATIONS_RUN', true),
    dbLogging: bool('DB_LOGGING', false),
    dbPoolSize: int('DB_POOL_SIZE', 10),
    redisUrl: str('REDIS_URL', 'redis://localhost:6379'),
    queuePrefix: str('QUEUE_PREFIX', 'clipforest'),
    eventsChannel: str('EVENTS_CHANNEL', 'clipforest:events'),

    jwtSecret: str('JWT_SECRET', isProd ? undefined : 'dev-only-insecure-secret-change-me'),
    sessionTtlHours: int('SESSION_TTL_HOURS', 24 * 7),
    cookieSecure: bool('COOKIE_SECURE', isProd),
    cookieName: 'cf_session',

    s3: {
      endpoint: process.env.S3_ENDPOINT || undefined,
      publicEndpoint: process.env.S3_PUBLIC_ENDPOINT || process.env.S3_ENDPOINT || undefined,
      region: str('S3_REGION', 'auto'),
      bucket: str('S3_BUCKET', 'clipforest'),
      accessKeyId: str('S3_ACCESS_KEY_ID', openapiOnly ? 'x' : undefined),
      secretAccessKey: str('S3_SECRET_ACCESS_KEY', openapiOnly ? 'x' : undefined),
      forcePathStyle: bool('S3_FORCE_PATH_STYLE', false),
    },
    uploadUrlTtlSec: int('UPLOAD_URL_TTL_SEC', 3600),
    playbackUrlTtlSec: int('PLAYBACK_URL_TTL_SEC', 3600),
    multipartThresholdBytes: int('MULTIPART_THRESHOLD_BYTES', 64 * 1024 * 1024),
    multipartPartSizeBytes: int('MULTIPART_PART_SIZE_BYTES', 16 * 1024 * 1024),
    partUrlBatchSize: int('PART_URL_BATCH_SIZE', 20),
    maxUploadBytes: int('MAX_UPLOAD_BYTES', 5 * 1024 * 1024 * 1024),
    maxVideoDurationSec: int('MAX_VIDEO_DURATION_SEC', 3 * 60 * 60),

    pipelineVersion: str('PIPELINE_VERSION', '2026-09-mvp1'),
    analysisVersion: str('ANALYSIS_VERSION', 'mvp1'),
    minCandidateScore: int('MIN_CANDIDATE_SCORE', 40),
    renderMinDurationMs: int('RENDER_MIN_DURATION_MS', 5_000),
    renderMaxDurationMs: int('RENDER_MAX_DURATION_MS', 180_000),
    allowedSourceHosts: list('ALLOWED_SOURCE_HOSTS', [
      'youtube.com',
      'www.youtube.com',
      'm.youtube.com',
      'youtu.be',
    ]),

    rateLimit: {
      defaultPerMinute: int('RATE_LIMIT_DEFAULT_PER_MIN', 300),
      authPerMinute: int('RATE_LIMIT_AUTH_PER_MIN', 20),
      createVideoPerMinute: int('RATE_LIMIT_CREATE_VIDEO_PER_MIN', 10),
      analyzePerMinute: int('RATE_LIMIT_ANALYZE_PER_MIN', 6),
      renderPerMinute: int('RATE_LIMIT_RENDER_PER_MIN', 30),
    },
    metricsToken: process.env.METRICS_TOKEN || undefined,
    logLevel: str('LOG_LEVEL', isProd ? 'info' : 'debug'),
  });
}

export type AppConfig = ReturnType<typeof loadConfig>;
export const config: AppConfig = loadConfig();
