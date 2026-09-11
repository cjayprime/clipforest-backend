import { CanActivate, ExecutionContext, Injectable } from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import { ThrottlerGuard } from '@nestjs/throttler';
import { config } from '../config';
import { IS_PUBLIC } from '../common/decorators';
import { Errors } from '../common/errors';
import { AuthService } from './auth.service';

/** Global guard: every route requires a valid session unless marked @Public(). */
@Injectable()
export class AuthGuard implements CanActivate {
  constructor(
    private readonly reflector: Reflector,
    private readonly auth: AuthService,
  ) {}

  canActivate(ctx: ExecutionContext): boolean {
    if (ctx.getType() !== 'http') return true;
    const req = ctx.switchToHttp().getRequest();
    const header: string | undefined = req.headers.authorization;
    const token: string | undefined =
      req.cookies?.[config.cookieName] ?? (header?.startsWith('Bearer ') ? header.slice(7) : undefined);
    const user = token ? this.auth.verify(token) : null;
    if (user) req.user = user;

    const isPublic = this.reflector.getAllAndOverride<boolean>(IS_PUBLIC, [ctx.getHandler(), ctx.getClass()]);
    if (isPublic) return true;
    if (!user) throw Errors.unauthorized();
    return true;
  }
}

/** Rate limits per authenticated user (falls back to client IP for public routes). */
@Injectable()
export class UserThrottlerGuard extends ThrottlerGuard {
  protected async getTracker(req: Record<string, any>): Promise<string> {
    return req.user?.id ?? req.ip;
  }
}
