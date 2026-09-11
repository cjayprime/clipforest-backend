import { Body, Controller, Delete, Get, HttpCode, Param, ParseUUIDPipe, Post, Query } from '@nestjs/common';
import { ApiTags } from '@nestjs/swagger';
import { Throttle } from '@nestjs/throttler';
import { config } from '../config';
import { AuthUser, CorrelationId, CurrentUser } from '../common/decorators';
import { CandidatesQueryDto, CreateVideoDto, ListVideosQueryDto, SignPartsDto, TranscriptQueryDto, UploadCompleteDto } from './videos.dto';
import { VideosService } from './videos.service';

const uuid = new ParseUUIDPipe({ version: '4' });

@ApiTags('videos')
@Controller('videos')
export class VideosController {
  constructor(private readonly videos: VideosService) {}

  @Get()
  list(@CurrentUser() user: AuthUser, @Query() q: ListVideosQueryDto) {
    return this.videos.list(user.id, q);
  }

  /** Create the durable video record before any long-running processing begins. */
  @Throttle({ default: { limit: config.rateLimit.createVideoPerMinute, ttl: 60_000 } })
  @Post()
  create(@CurrentUser() user: AuthUser, @Body() dto: CreateVideoDto, @CorrelationId() cid: string) {
    return this.videos.create(user, dto, cid);
  }

  @Get(':id')
  get(@CurrentUser() user: AuthUser, @Param('id', uuid) id: string) {
    return this.videos.get(user.id, id);
  }

  /** Signed direct-to-storage upload information (single PUT or multipart). The file never transits the API. */
  @Post(':id/upload-session')
  @HttpCode(200)
  uploadSession(@CurrentUser() user: AuthUser, @Param('id', uuid) id: string) {
    return this.videos.createUploadSession(user.id, id);
  }

  @Post(':id/upload-session/parts')
  @HttpCode(200)
  signParts(@CurrentUser() user: AuthUser, @Param('id', uuid) id: string, @Body() dto: SignPartsDto) {
    return this.videos.signParts(user.id, id, dto.partNumbers);
  }

  /** Idempotent; verifies the object exists and enqueues ingestion exactly once. */
  @Post(':id/upload-complete')
  @HttpCode(200)
  uploadComplete(
    @CurrentUser() user: AuthUser,
    @Param('id', uuid) id: string,
    @Body() dto: UploadCompleteDto,
    @CorrelationId() cid: string,
  ) {
    return this.videos.completeUpload(user.id, id, dto, cid);
  }

  @Post(':id/process')
  @HttpCode(202)
  process(@CurrentUser() user: AuthUser, @Param('id', uuid) id: string, @CorrelationId() cid: string) {
    return this.videos.process(user.id, id, cid);
  }

  @Throttle({ default: { limit: config.rateLimit.analyzePerMinute, ttl: 60_000 } })
  @Post(':id/analyze')
  @HttpCode(202)
  analyze(@CurrentUser() user: AuthUser, @Param('id', uuid) id: string, @CorrelationId() cid: string) {
    return this.videos.analyze(user.id, id, cid);
  }

  @Get(':id/candidates')
  candidates(@CurrentUser() user: AuthUser, @Param('id', uuid) id: string, @Query() q: CandidatesQueryDto) {
    return this.videos.candidates(user.id, id, q);
  }

  @Get(':id/transcript')
  transcript(@CurrentUser() user: AuthUser, @Param('id', uuid) id: string, @Query() q: TranscriptQueryDto) {
    return this.videos.transcript(user.id, id, q);
  }

  @Get(':id/playback')
  playback(@CurrentUser() user: AuthUser, @Param('id', uuid) id: string) {
    return this.videos.playback(user.id, id);
  }

  @Delete(':id')
  @HttpCode(202)
  remove(@CurrentUser() user: AuthUser, @Param('id', uuid) id: string, @CorrelationId() cid: string) {
    return this.videos.remove(user.id, id, cid);
  }
}
