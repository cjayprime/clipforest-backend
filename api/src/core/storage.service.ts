import {
  AbortMultipartUploadCommand,
  CompleteMultipartUploadCommand,
  CreateMultipartUploadCommand,
  GetObjectCommand,
  HeadBucketCommand,
  HeadObjectCommand,
  PutObjectCommand,
  S3Client,
  UploadPartCommand,
} from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';
import { Injectable, Logger } from '@nestjs/common';
import { config } from '../config';
import { Errors } from '../common/errors';

/**
 * R2 / S3-compatible storage. Two clients: `internal` talks to the storage
 * endpoint reachable from the API container; `presigner` signs URLs against the
 * endpoint the *browser* can reach (identical for R2, different for local MinIO).
 * Master credentials never leave the server; browsers only get short-lived URLs.
 */
@Injectable()
export class StorageService {
  private readonly logger = new Logger(StorageService.name);
  private readonly internal: S3Client;
  private readonly presigner: S3Client;
  readonly bucket = config.s3.bucket;

  constructor() {
    const base = {
      region: config.s3.region,
      forcePathStyle: config.s3.forcePathStyle,
      credentials: { accessKeyId: config.s3.accessKeyId, secretAccessKey: config.s3.secretAccessKey },
      // Presigned browser uploads cannot compute SDK checksums; only send them when required.
      requestChecksumCalculation: 'WHEN_REQUIRED' as const,
      responseChecksumValidation: 'WHEN_REQUIRED' as const,
    };
    this.internal = new S3Client({ ...base, endpoint: config.s3.endpoint });
    this.presigner = new S3Client({ ...base, endpoint: config.s3.publicEndpoint });
  }

  presignPut(key: string, contentType: string, ttlSec = config.uploadUrlTtlSec): Promise<string> {
    return getSignedUrl(this.presigner, new PutObjectCommand({ Bucket: this.bucket, Key: key, ContentType: contentType }), {
      expiresIn: ttlSec,
    });
  }

  async createMultipart(key: string, contentType: string): Promise<string> {
    const out = await this.wrap(() =>
      this.internal.send(new CreateMultipartUploadCommand({ Bucket: this.bucket, Key: key, ContentType: contentType })),
    );
    if (!out.UploadId) throw Errors.storage('Storage did not return an upload ID.');
    return out.UploadId;
  }

  presignPart(key: string, uploadId: string, partNumber: number, ttlSec = config.uploadUrlTtlSec): Promise<string> {
    return getSignedUrl(
      this.presigner,
      new UploadPartCommand({ Bucket: this.bucket, Key: key, UploadId: uploadId, PartNumber: partNumber }),
      { expiresIn: ttlSec },
    );
  }

  /** Returns false when the upload no longer exists (e.g. already completed by an earlier retried call). */
  async completeMultipart(key: string, uploadId: string, parts: { partNumber: number; etag: string }[]): Promise<boolean> {
    try {
      await this.internal.send(
        new CompleteMultipartUploadCommand({
          Bucket: this.bucket,
          Key: key,
          UploadId: uploadId,
          MultipartUpload: {
            Parts: [...parts]
              .sort((a, b) => a.partNumber - b.partNumber)
              .map((p) => ({ PartNumber: p.partNumber, ETag: p.etag })),
          },
        }),
      );
      return true;
    } catch (err) {
      const name = (err as { name?: string }).name;
      if (name === 'NoSuchUpload') return false;
      this.logger.warn({ err, key }, 'CompleteMultipartUpload failed');
      throw Errors.storage('Could not finalize the upload. Please retry.');
    }
  }

  async abortMultipart(key: string, uploadId: string): Promise<void> {
    try {
      await this.internal.send(new AbortMultipartUploadCommand({ Bucket: this.bucket, Key: key, UploadId: uploadId }));
    } catch (err) {
      this.logger.warn({ err, key }, 'AbortMultipartUpload failed (ignored)');
    }
  }

  async head(key: string): Promise<{ size: number; contentType?: string } | null> {
    try {
      const out = await this.internal.send(new HeadObjectCommand({ Bucket: this.bucket, Key: key }));
      return { size: Number(out.ContentLength ?? 0), contentType: out.ContentType };
    } catch (err) {
      const e = err as { name?: string; $metadata?: { httpStatusCode?: number } };
      if (e.name === 'NotFound' || e.name === 'NoSuchKey' || e.$metadata?.httpStatusCode === 404) return null;
      this.logger.warn({ err, key }, 'HeadObject failed');
      throw Errors.storage('Storage is temporarily unavailable.');
    }
  }

  presignGet(key: string, opts: { ttlSec?: number; downloadName?: string } = {}): Promise<string> {
    const disposition = opts.downloadName
      ? `attachment; filename="${opts.downloadName.replace(/[^A-Za-z0-9._ -]/g, '_')}"`
      : undefined;
    return getSignedUrl(
      this.presigner,
      new GetObjectCommand({ Bucket: this.bucket, Key: key, ResponseContentDisposition: disposition }),
      { expiresIn: opts.ttlSec ?? config.playbackUrlTtlSec },
    );
  }

  async check(): Promise<boolean> {
    try {
      await this.internal.send(new HeadBucketCommand({ Bucket: this.bucket }));
      return true;
    } catch {
      return false;
    }
  }

  private async wrap<T>(fn: () => Promise<T>): Promise<T> {
    try {
      return await fn();
    } catch (err) {
      this.logger.warn({ err }, 'Storage call failed');
      throw Errors.storage('Storage is temporarily unavailable.');
    }
  }
}
