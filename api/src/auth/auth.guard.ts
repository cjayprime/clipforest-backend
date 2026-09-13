import { CanActivate, ExecutionContext, Injectable } from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import { ThrottlerGuard } from '@nestjs/throttler';
import { config } from '../config';
import { IS_PUBLIC, type RequestWithUser } from '../common/decorators';
import { Errors } from '../common/errors';
import { AuthService } from './auth.service';

/** Global guard: every route requires a valid session unless marked @Public(). */
@Injectable()
export class AuthGuard implements CanActivate {
  constructor(
    private readonly reflector: Reflector,
    private readonly auth: AuthService,
  ) {}

  async canActivate(ctx: ExecutionContext): Promise<boolean> {
    if (ctx.getType() !== 'http') return true;
    const req = ctx.switchToHttp().getRequest<RequestWithUser>();
    const header = req.headers.authorization;
    const cookie: unknown = req.cookies[config.cookieName];
    const token =
      typeof cookie === 'string' ? cookie : header?.startsWith('Bearer ') ? header.slice(7) : undefined;

    const session = token ? this.auth.verify(token) : null;
    // A valid signature is not enough: a password change invalidates sessions
    // issued before it, so this one may belong to a device that was signed out.
    const live = session && !(await this.auth.isSessionRevoked(session)) ? session : null;
    if (live) req.user = { id: live.id, email: live.email };

    const isPublic = this.reflector.getAllAndOverride<boolean>(IS_PUBLIC, [ctx.getHandler(), ctx.getClass()]);
    if (isPublic) return true;
    if (!live) throw Errors.unauthorized();
    return true;
  }
}

/** Rate limits per authenticated user (falls back to client IP for public routes). */
@Injectable()
export class UserThrottlerGuard extends ThrottlerGuard {
  protected getTracker(req: RequestWithUser): Promise<string> {
    return Promise.resolve(req.user?.id ?? req.ip ?? 'unknown');
  }
}
