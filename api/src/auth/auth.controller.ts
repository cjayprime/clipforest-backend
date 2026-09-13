import { Body, Controller, Get, HttpCode, Post, Res } from '@nestjs/common';
import { ApiOperation, ApiTags } from '@nestjs/swagger';
import { Throttle } from '@nestjs/throttler';
import type { Response } from 'express';
import { config } from '../config';
import { AuthUser, CurrentUser, Public } from '../common/decorators';
import { AuthService } from './auth.service';
import { ChangePasswordDto, ForgotPasswordDto, LoginDto, RegisterDto, ResetPasswordDto } from './auth.dto';

@ApiTags('auth')
@Controller('auth')
export class AuthController {
  constructor(private readonly auth: AuthService) {}

  private setSession(res: Response, user: { user_id: string; email: string }) {
    res.cookie(config.cookieName, this.auth.sign(user), {
      httpOnly: true,
      sameSite: 'lax',
      secure: config.cookieSecure,
      maxAge: config.sessionTtlHours * 3600 * 1000,
      path: '/',
    });
  }

  @Public()
  @Throttle({ default: { limit: config.rateLimit.authPerMinute, ttl: 60_000 } })
  @Post('register')
  async register(@Body() dto: RegisterDto, @Res({ passthrough: true }) res: Response) {
    const user = await this.auth.register(dto.email, dto.password, dto.displayName);
    this.setSession(res, user);
    return { user: await this.auth.profile(user.user_id) };
  }

  @Public()
  @Throttle({ default: { limit: config.rateLimit.authPerMinute, ttl: 60_000 } })
  @Post('login')
  @HttpCode(200)
  async login(@Body() dto: LoginDto, @Res({ passthrough: true }) res: Response) {
    const user = await this.auth.login(dto.email, dto.password);
    this.setSession(res, user);
    return { user: await this.auth.profile(user.user_id) };
  }

  @Public()
  @Post('logout')
  @HttpCode(200)
  logout(@Res({ passthrough: true }) res: Response) {
    res.clearCookie(config.cookieName, { path: '/' });
    return { ok: true };
  }

  @Public()
  @Throttle({ default: { limit: config.rateLimit.authPerMinute, ttl: 60_000 } })
  @Post('forgot-password')
  @HttpCode(202)
  @ApiOperation({
    summary: 'Send a password reset link',
    description:
      'Always accepted, whether or not the address has an account, so the response cannot be used to discover ' +
      'registered users. Delivery failures are logged server-side for the same reason.',
  })
  async forgotPassword(@Body() dto: ForgotPasswordDto) {
    await this.auth.requestPasswordReset(dto.email);
    return { ok: true };
  }

  @Public()
  @Throttle({ default: { limit: config.rateLimit.authPerMinute, ttl: 60_000 } })
  @Post('reset-password')
  @HttpCode(200)
  @ApiOperation({
    summary: 'Redeem a password reset link',
    description:
      'Single use and time limited. On success the caller is signed in and every session issued before the ' +
      'change stops working.',
  })
  async resetPassword(@Body() dto: ResetPasswordDto, @Res({ passthrough: true }) res: Response) {
    const user = await this.auth.resetPassword(dto.token, dto.password);
    this.setSession(res, user);
    return { user: await this.auth.profile(user.user_id) };
  }

  @Throttle({ default: { limit: config.rateLimit.authPerMinute, ttl: 60_000 } })
  @Post('change-password')
  @HttpCode(200)
  @ApiOperation({
    summary: 'Change the signed-in account password',
    description: 'Requires the current password. Other devices are signed out; this one is issued a fresh session.',
  })
  async changePassword(
    @CurrentUser() current: AuthUser,
    @Body() dto: ChangePasswordDto,
    @Res({ passthrough: true }) res: Response,
  ) {
    const user = await this.auth.changePassword(current.id, dto.currentPassword, dto.newPassword);
    this.setSession(res, user);
    return { user: await this.auth.profile(user.user_id) };
  }

  @Get('me')
  async me(@CurrentUser() user: AuthUser) {
    // AuthUser is the session principal, not the entity: its identifier is `id`.
    return { user: await this.auth.profile(user.id) };
  }
}
