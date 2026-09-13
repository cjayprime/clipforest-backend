import { Body, Controller, Delete, Get, HttpCode, Param, Patch, Post, Query } from '@nestjs/common';
import { ApiTags } from '@nestjs/swagger';
import { Throttle } from '@nestjs/throttler';
import { config } from '../config';
import { AuthUser, CorrelationId, CurrentUser } from '../common/decorators';
import { EntityIdPipe } from '../common/entity-id.pipe';
import { CreateRenderDto, ListRendersQueryDto, ManualRenderDto, UpdateRenderDto } from './renders.dto';
import { RendersService } from './renders.service';

const entityId = new EntityIdPipe();
const renderLimit = { default: { limit: config.rateLimit.renderPerMinute, ttl: 60_000 } };

@ApiTags('renders')
@Controller()
export class RendersController {
  constructor(private readonly renders: RendersService) {}

  /** Generate Clip: creates a render from an immutable snapshot of the candidate range + settings. */
  @Throttle(renderLimit)
  @Post('candidates/:id/renders')
  @HttpCode(202)
  fromCandidate(@CurrentUser() user: AuthUser, @Param('id', entityId) id: string, @Body() dto: CreateRenderDto, @CorrelationId() cid: string) {
    return this.renders.fromCandidate(user, id, dto, cid);
  }

  /** Manual range render (no-candidate state) or continuation of an existing lineage. */
  @Throttle(renderLimit)
  @Post('videos/:id/renders')
  @HttpCode(202)
  manual(@CurrentUser() user: AuthUser, @Param('id', entityId) id: string, @Body() dto: ManualRenderDto, @CorrelationId() cid: string) {
    return this.renders.manual(user, id, dto, cid);
  }

  @Get('videos/:id/renders')
  forVideo(@CurrentUser() user: AuthUser, @Param('id', entityId) id: string) {
    return this.renders.list(user.id, id);
  }

  @Get('renders')
  list(@CurrentUser() user: AuthUser, @Query() q: ListRendersQueryDto) {
    return this.renders.list(user.id, q.videoId);
  }

  /** Render status + output; signed playback/download URLs are re-issued on every read. */
  @Get('renders/:id')
  get(@CurrentUser() user: AuthUser, @Param('id', entityId) id: string) {
    return this.renders.get(user.id, id);
  }

  @Patch('renders/:id')
  update(@CurrentUser() user: AuthUser, @Param('id', entityId) id: string, @Body() dto: UpdateRenderDto) {
    return this.renders.updateTitle(user.id, id, dto.title);
  }

  @Throttle(renderLimit)
  @Post('renders/:id/rerender')
  @HttpCode(202)
  rerender(@CurrentUser() user: AuthUser, @Param('id', entityId) id: string, @Body() dto: CreateRenderDto, @CorrelationId() cid: string) {
    return this.renders.rerender(user, id, dto, cid);
  }

  @Post('renders/:id/retry')
  @HttpCode(202)
  retry(@CurrentUser() user: AuthUser, @Param('id', entityId) id: string, @CorrelationId() cid: string) {
    return this.renders.retry(user.id, id, cid);
  }

  @Delete('renders/:id')
  @HttpCode(202)
  remove(@CurrentUser() user: AuthUser, @Param('id', entityId) id: string) {
    return this.renders.remove(user.id, id);
  }
}
