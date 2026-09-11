const MIB = 1024 * 1024;
const MIN_PART = 5 * MIB;
const MAX_PARTS = 10_000;

/**
 * Plans a multipart upload. R2 requires every part except the last to be the
 * same size, and S3-compatible stores cap uploads at 10,000 parts, so the part
 * size grows (in whole MiB) for very large files.
 */
export function planMultipart(sizeBytes: number, preferredPartBytes: number) {
  let partSize = Math.max(preferredPartBytes, MIN_PART);
  if (Math.ceil(sizeBytes / partSize) > MAX_PARTS) {
    partSize = Math.ceil(sizeBytes / MAX_PARTS / MIB) * MIB;
  }
  return { partSize, partCount: Math.max(1, Math.ceil(sizeBytes / partSize)) };
}
