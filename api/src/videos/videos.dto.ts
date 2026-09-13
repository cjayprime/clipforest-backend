import { Type } from 'class-transformer';
import {
  ArrayMaxSize,
  ArrayMinSize,
  IsArray,
  IsBoolean,
  IsIn,
  IsInt,
  IsOptional,
  IsString,
  Max,
  MaxLength,
  Min,
  ValidateIf,
  ValidateNested,
} from 'class-validator';

export class CreateVideoDto {
  /** "upload" for direct-to-storage upload, "url" for a supported source link. */
  @IsIn(['upload', 'url'])
  sourceType: 'upload' | 'url';

  @ValidateIf((o: CreateVideoDto) => o.sourceType === 'upload')
  @IsString()
  @MaxLength(255)
  originalFilename?: string;

  @ValidateIf((o: CreateVideoDto) => o.sourceType === 'upload')
  @IsString()
  @MaxLength(100)
  contentType?: string;

  @ValidateIf((o: CreateVideoDto) => o.sourceType === 'upload')
  @IsInt()
  @Min(1)
  sizeBytes?: number;

  @ValidateIf((o: CreateVideoDto) => o.sourceType === 'url')
  @IsString()
  @MaxLength(2048)
  sourceUrl?: string;

  @IsOptional()
  @IsString()
  @MaxLength(200)
  title?: string;

  /** The user confirms they own or are authorized to process this content. */
  @IsBoolean()
  rightsConfirmed: boolean;
}

export class ListVideosQueryDto {
  @IsOptional()
  @IsIn(['all', 'processing', 'ready', 'failed'])
  filter?: 'all' | 'processing' | 'ready' | 'failed';
}

/** Asks for a further batch of signed multipart URLs. */
export class SignPartsDto {
  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(100)
  @IsInt({ each: true })
  @Min(1, { each: true })
  partNumbers: number[];
}

/** One finished multipart part, as reported by the browser. */
export class CompletedPartDto {
  @IsInt()
  @Min(1)
  partNumber: number;

  @IsString()
  @MaxLength(200)
  etag: string;
}

export class UploadCompleteDto {
  @IsOptional()
  @IsArray()
  @ArrayMaxSize(10_000)
  @ValidateNested({ each: true })
  @Type(() => CompletedPartDto)
  parts?: CompletedPartDto[];

  /** Size the client observed locally (informational; storage is authoritative). */
  @IsOptional()
  @IsInt()
  @Min(0)
  observedSizeBytes?: number;
}

export class CandidatesQueryDto {
  @IsOptional()
  @IsIn(['score', 'time'])
  sort?: 'score' | 'time';

  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @Min(0)
  @Max(100)
  minScore?: number;
}

/** A window of the transcript; the service caps how wide it may be. */
export class TranscriptQueryDto {
  @Type(() => Number)
  @IsInt()
  @Min(0)
  startMs: number;

  @Type(() => Number)
  @IsInt()
  @Min(1)
  endMs: number;
}
