import { createParamDecorator, ExecutionContext, SetMetadata } from '@nestjs/common';
import type { Request } from 'express';

/** The authenticated caller, as attached to the request by AuthGuard. */
export interface AuthUser {
  id: string;
  email: string;
}

/**
 * What Nest's `getRequest()` actually hands back on an HTTP request. It is typed
 * `any` by default, which quietly defeats checking on `req.user`.
 *
 * `user` is only attached once AuthGuard has verified a session. `id` is NOT
 * declared: pino-http already augments Request with a required `id: ReqId`, and
 * redeclaring it as optional conflicts with that ambient type.
 */
export interface RequestWithUser extends Request {
  user?: AuthUser;
}

export const IS_PUBLIC = 'isPublic';

/** Marks a route as not requiring authentication. */
export const Public = () => SetMetadata(IS_PUBLIC, true);

/**
 * The authenticated caller. Only valid on routes the guard has protected — on a
 * `@Public()` route there may be no user.
 */
export const CurrentUser = createParamDecorator((_data: unknown, ctx: ExecutionContext): AuthUser => {
  const user = ctx.switchToHttp().getRequest<RequestWithUser>().user;
  if (!user) throw new Error('@CurrentUser() used on a route with no authenticated user');
  return user;
});

/**
 * The per-request correlation ID, echoed in every error envelope. pino-http types
 * `id` as ReqId (which includes object), so it is narrowed, not stringified.
 */
export const CorrelationId = createParamDecorator((_data: unknown, ctx: ExecutionContext): string => {
  const { id } = ctx.switchToHttp().getRequest<RequestWithUser>();
  return typeof id === 'string' ? id : typeof id === 'number' ? String(id) : '';
});
