import { Type } from 'class-transformer';
import { IsBoolean, IsIn, IsInt, IsOptional, IsString, Matches, MaxLength, Min, MinLength, ValidateNested } from 'class-validator';
import { ENTITY_ID_PATTERN } from '../common/entity-id.pipe';
import { ASPECT_RATIO_KEYS, CAPTION_PRESETS, FRAMING_MODES, type AspectRatio } from '../common/render-settings';

export class CaptionsDto {
  @IsBoolean()
  enabled: boolean;

  @IsOptional()
  @IsIn(CAPTION_PRESETS)
  preset?: (typeof CAPTION_PRESETS)[number];
}

/** Settings snapshot for a render (PRD §12.2). Omitted fields fall back to the candidate/parent render. */
export class CreateRenderDto {
  @IsOptional()
  @IsInt()
  @Min(0)
  startMs?: number;

  @IsOptional()
  @IsInt()
  @Min(1)
  endMs?: number;

  @IsOptional()
  @IsIn(ASPECT_RATIO_KEYS)
  aspectRatio?: AspectRatio;

  @IsOptional()
  @IsIn(FRAMING_MODES)
  framingMode?: (typeof FRAMING_MODES)[number];

  @IsOptional()
  @ValidateNested()
  @Type(() => CaptionsDto)
  captions?: CaptionsDto;

  @IsOptional()
  @IsString()
  @MaxLength(120)
  title?: string;

  /** Render again even if an identical completed render exists. */
  @IsOptional()
  @IsBoolean()
  force?: boolean;
}

export class ManualRenderDto extends CreateRenderDto {
  /** Continue an existing render lineage (version history) instead of starting a new one. */
  @IsOptional()
  @Matches(ENTITY_ID_PATTERN, { message: 'parentRenderId must be a valid identifier' })
  parentRenderId?: string;
}

export class UpdateRenderDto {
  @IsString()
  @MinLength(1)
  @MaxLength(120)
  title: string;
}

export class ListRendersQueryDto {
  @IsOptional()
  @Matches(ENTITY_ID_PATTERN, { message: 'videoId must be a valid identifier' })
  videoId?: string;
}
