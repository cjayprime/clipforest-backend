import { Type } from 'class-transformer';
import { IsBoolean, IsIn, IsInt, IsOptional, IsString, IsUUID, MaxLength, Min, MinLength, ValidateNested } from 'class-validator';
import { CAPTION_PRESETS, FRAMING_MODES } from '../common/render-settings';

export class CaptionsDto {
  @IsBoolean()
  enabled: boolean;

  @IsOptional()
  @IsIn(CAPTION_PRESETS as unknown as string[])
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
  @IsIn(['9:16'])
  aspectRatio?: '9:16';

  @IsOptional()
  @IsIn(FRAMING_MODES as unknown as string[])
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
  @IsUUID('4')
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
  @IsUUID('4')
  videoId?: string;
}
