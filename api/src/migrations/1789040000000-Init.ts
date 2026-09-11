import { MigrationInterface, QueryRunner } from 'typeorm';

/**
 * Initial schema (PRD §13). Written by hand because the Python media worker
 * shares these tables: names, types and constraints are a cross-runtime contract.
 */
export class Init1789040000000 implements MigrationInterface {
  name = 'Init1789040000000';

  public async up(q: QueryRunner): Promise<void> {
    await q.query(`CREATE EXTENSION IF NOT EXISTS pgcrypto`); // gen_random_uuid()

    await q.query(`
      CREATE TABLE "users" (
        "id" uuid NOT NULL DEFAULT gen_random_uuid(),
        "email" text NOT NULL,
        "password_hash" text NOT NULL,
        "display_name" text,
        "plan" text NOT NULL DEFAULT 'free',
        "limits" jsonb,
        "created_at" timestamptz(3) NOT NULL DEFAULT now(),
        "updated_at" timestamptz(3) NOT NULL DEFAULT now(),
        CONSTRAINT "users_pkey" PRIMARY KEY ("id"),
        CONSTRAINT "users_email_key" UNIQUE ("email")
      )`);

    await q.query(`
      CREATE TABLE "videos" (
        "id" uuid NOT NULL DEFAULT gen_random_uuid(),
        "user_id" uuid NOT NULL,
        "source_type" text NOT NULL,
        "source_url" text,
        "source_provider" text,
        "object_key" text,
        "original_filename" text NOT NULL,
        "title" text NOT NULL,
        "content_type" text,
        "size_bytes" bigint,
        "duration_ms" integer,
        "width" integer,
        "height" integer,
        "fps" double precision,
        "orientation" text,
        "has_audio" boolean,
        "video_codec" text,
        "audio_codec" text,
        "container" text,
        "language" text,
        "status" text NOT NULL DEFAULT 'CREATED',
        "progress" integer NOT NULL DEFAULT 0,
        "stage" text,
        "substage" text,
        "pipeline_version" text NOT NULL,
        "processing_run" integer NOT NULL DEFAULT 0,
        "analysis_run" integer NOT NULL DEFAULT 0,
        "error_code" text,
        "error_message" text,
        "error_retryable" boolean,
        "error_correlation_id" text,
        "failed_stage" text,
        "thumbnail_key" text,
        "proxy_key" text,
        "audio_key" text,
        "upload_id" text,
        "upload_completed_at" timestamptz(3),
        "rights_confirmed_at" timestamptz(3) NOT NULL,
        "active_transcript_id" uuid,
        "ready_at" timestamptz(3),
        "source_expired_at" timestamptz(3),
        "deleted_at" timestamptz(3),
        "purged_at" timestamptz(3),
        "created_at" timestamptz(3) NOT NULL DEFAULT now(),
        "updated_at" timestamptz(3) NOT NULL DEFAULT now(),
        CONSTRAINT "videos_pkey" PRIMARY KEY ("id"),
        CONSTRAINT "videos_active_transcript_id_key" UNIQUE ("active_transcript_id"),
        CONSTRAINT "videos_status_check" CHECK ("status" IN ('CREATED','UPLOADING','QUEUED','INGESTING','TRANSCRIBING','ANALYZING','READY','FAILED')),
        CONSTRAINT "videos_source_type_check" CHECK ("source_type" IN ('upload','url')),
        CONSTRAINT "videos_progress_check" CHECK ("progress" BETWEEN 0 AND 100),
        CONSTRAINT "videos_duration_check" CHECK ("duration_ms" IS NULL OR "duration_ms" >= 0)
      )`);
    await q.query(`CREATE INDEX "videos_user_id_created_at_idx" ON "videos" ("user_id", "created_at" DESC)`);
    await q.query(`CREATE INDEX "videos_status_idx" ON "videos" ("status")`);

    await q.query(`
      CREATE TABLE "transcripts" (
        "id" uuid NOT NULL DEFAULT gen_random_uuid(),
        "video_id" uuid NOT NULL,
        "version" integer NOT NULL,
        "provider" text NOT NULL,
        "language" text,
        "full_text" text NOT NULL DEFAULT '',
        "segments" jsonb NOT NULL DEFAULT '[]'::jsonb,
        "words" jsonb NOT NULL DEFAULT '[]'::jsonb,
        "word_count" integer NOT NULL DEFAULT 0,
        "duration_ms" integer,
        "provider_job_id" text,
        "provider_metadata" jsonb,
        "status" text NOT NULL DEFAULT 'PENDING',
        "error_code" text,
        "created_at" timestamptz(3) NOT NULL DEFAULT now(),
        "updated_at" timestamptz(3) NOT NULL DEFAULT now(),
        CONSTRAINT "transcripts_pkey" PRIMARY KEY ("id"),
        CONSTRAINT "transcripts_video_id_version_key" UNIQUE ("video_id", "version"),
        CONSTRAINT "transcripts_status_check" CHECK ("status" IN ('PENDING','PROCESSING','COMPLETED','FAILED'))
      )`);

    await q.query(`
      CREATE TABLE "candidates" (
        "id" uuid NOT NULL DEFAULT gen_random_uuid(),
        "video_id" uuid NOT NULL,
        "transcript_id" uuid,
        "start_ms" integer NOT NULL,
        "end_ms" integer NOT NULL,
        "title" text NOT NULL,
        "hook_text" text NOT NULL,
        "excerpt" text NOT NULL,
        "summary" text NOT NULL,
        "reason" text NOT NULL,
        "category" text NOT NULL,
        "score" integer NOT NULL,
        "component_scores" jsonb NOT NULL,
        "analysis_version" text NOT NULL,
        "analysis_run" integer NOT NULL,
        "provider" text,
        "rank" integer NOT NULL,
        "superseded_at" timestamptz(3),
        "created_at" timestamptz(3) NOT NULL DEFAULT now(),
        CONSTRAINT "candidates_pkey" PRIMARY KEY ("id"),
        CONSTRAINT "candidates_range_check" CHECK ("start_ms" >= 0 AND "start_ms" < "end_ms"),
        CONSTRAINT "candidates_score_check" CHECK ("score" BETWEEN 0 AND 100),
        CONSTRAINT "candidates_category_check" CHECK ("category" IN ('insight','story','humor','controversy','advice','reaction','other'))
      )`);
    await q.query(`CREATE INDEX "candidates_video_id_superseded_at_score_idx" ON "candidates" ("video_id", "superseded_at", "score")`);

    await q.query(`
      CREATE TABLE "renders" (
        "id" uuid NOT NULL DEFAULT gen_random_uuid(),
        "video_id" uuid NOT NULL,
        "user_id" uuid NOT NULL,
        "candidate_id" uuid,
        "parent_render_id" uuid,
        "lineage_key" text NOT NULL,
        "version" integer NOT NULL,
        "is_latest" boolean NOT NULL DEFAULT true,
        "title" text NOT NULL,
        "start_ms" integer NOT NULL,
        "end_ms" integer NOT NULL,
        "settings" jsonb NOT NULL,
        "settings_hash" text NOT NULL,
        "pipeline_version" text NOT NULL,
        "status" text NOT NULL DEFAULT 'QUEUED',
        "progress" integer NOT NULL DEFAULT 0,
        "stage" text,
        "substage" text,
        "attempt" integer NOT NULL DEFAULT 1,
        "output_key" text,
        "thumbnail_key" text,
        "width" integer,
        "height" integer,
        "duration_ms" integer,
        "file_size" bigint,
        "framing" jsonb,
        "error_code" text,
        "error_message" text,
        "error_retryable" boolean,
        "error_correlation_id" text,
        "started_at" timestamptz(3),
        "completed_at" timestamptz(3),
        "deleted_at" timestamptz(3),
        "created_at" timestamptz(3) NOT NULL DEFAULT now(),
        "updated_at" timestamptz(3) NOT NULL DEFAULT now(),
        CONSTRAINT "renders_pkey" PRIMARY KEY ("id"),
        CONSTRAINT "renders_lineage_key_version_key" UNIQUE ("lineage_key", "version"),
        CONSTRAINT "renders_status_check" CHECK ("status" IN ('QUEUED','PREPARING','ANALYZING_VISUALS','RENDERING','UPLOADING','COMPLETED','FAILED')),
        CONSTRAINT "renders_range_check" CHECK ("start_ms" >= 0 AND "start_ms" < "end_ms"),
        CONSTRAINT "renders_progress_check" CHECK ("progress" BETWEEN 0 AND 100)
      )`);
    await q.query(`CREATE INDEX "renders_user_id_created_at_idx" ON "renders" ("user_id", "created_at" DESC)`);
    await q.query(`CREATE INDEX "renders_video_id_idx" ON "renders" ("video_id")`);
    await q.query(`CREATE INDEX "renders_settings_hash_idx" ON "renders" ("settings_hash")`);
    await q.query(`CREATE INDEX "renders_lineage_latest_idx" ON "renders" ("lineage_key") WHERE "is_latest"`);

    await q.query(`
      CREATE TABLE "job_runs" (
        "id" uuid NOT NULL DEFAULT gen_random_uuid(),
        "queue_name" text NOT NULL,
        "external_job_id" text NOT NULL,
        "entity_type" text NOT NULL,
        "entity_id" text NOT NULL,
        "attempt" integer NOT NULL,
        "status" text NOT NULL,
        "started_at" timestamptz(3) NOT NULL DEFAULT now(),
        "completed_at" timestamptz(3),
        "duration_ms" integer,
        "error_code" text,
        "error_message" text,
        "metadata" jsonb,
        CONSTRAINT "job_runs_pkey" PRIMARY KEY ("id"),
        CONSTRAINT "job_runs_queue_job_attempt_key" UNIQUE ("queue_name", "external_job_id", "attempt")
      )`);
    await q.query(`CREATE INDEX "job_runs_entity_idx" ON "job_runs" ("entity_type", "entity_id")`);

    await q.query(`
      CREATE TABLE "usage_events" (
        "id" uuid NOT NULL DEFAULT gen_random_uuid(),
        "user_id" uuid NOT NULL,
        "event_type" text NOT NULL,
        "video_id" uuid,
        "render_id" uuid,
        "units" double precision NOT NULL,
        "unit" text NOT NULL,
        "idempotency_key" text NOT NULL,
        "created_at" timestamptz(3) NOT NULL DEFAULT now(),
        CONSTRAINT "usage_events_pkey" PRIMARY KEY ("id"),
        CONSTRAINT "usage_events_idempotency_key_key" UNIQUE ("idempotency_key")
      )`);
    await q.query(`CREATE INDEX "usage_events_user_id_created_at_idx" ON "usage_events" ("user_id", "created_at")`);

    // Deleting a video cascades to its transcripts, candidates and renders (PRD §13.1).
    await q.query(`ALTER TABLE "videos" ADD CONSTRAINT "videos_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE`);
    await q.query(`ALTER TABLE "transcripts" ADD CONSTRAINT "transcripts_video_id_fkey" FOREIGN KEY ("video_id") REFERENCES "videos"("id") ON DELETE CASCADE`);
    await q.query(`ALTER TABLE "videos" ADD CONSTRAINT "videos_active_transcript_id_fkey" FOREIGN KEY ("active_transcript_id") REFERENCES "transcripts"("id") ON DELETE SET NULL`);
    await q.query(`ALTER TABLE "candidates" ADD CONSTRAINT "candidates_video_id_fkey" FOREIGN KEY ("video_id") REFERENCES "videos"("id") ON DELETE CASCADE`);
    await q.query(`ALTER TABLE "candidates" ADD CONSTRAINT "candidates_transcript_id_fkey" FOREIGN KEY ("transcript_id") REFERENCES "transcripts"("id") ON DELETE SET NULL`);
    await q.query(`ALTER TABLE "renders" ADD CONSTRAINT "renders_video_id_fkey" FOREIGN KEY ("video_id") REFERENCES "videos"("id") ON DELETE CASCADE`);
    await q.query(`ALTER TABLE "renders" ADD CONSTRAINT "renders_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE`);
    await q.query(`ALTER TABLE "renders" ADD CONSTRAINT "renders_candidate_id_fkey" FOREIGN KEY ("candidate_id") REFERENCES "candidates"("id") ON DELETE SET NULL`);
    // The usage ledger outlives the content it refers to (billing history).
    await q.query(`ALTER TABLE "usage_events" ADD CONSTRAINT "usage_events_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE`);
  }

  public async down(q: QueryRunner): Promise<void> {
    await q.query(`DROP TABLE IF EXISTS "usage_events"`);
    await q.query(`DROP TABLE IF EXISTS "job_runs"`);
    await q.query(`DROP TABLE IF EXISTS "renders"`);
    await q.query(`DROP TABLE IF EXISTS "candidates"`);
    await q.query(`ALTER TABLE "videos" DROP CONSTRAINT IF EXISTS "videos_active_transcript_id_fkey"`);
    await q.query(`DROP TABLE IF EXISTS "transcripts"`);
    await q.query(`DROP TABLE IF EXISTS "videos"`);
    await q.query(`DROP TABLE IF EXISTS "users"`);
  }
}
