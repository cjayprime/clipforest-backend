import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import * as bcrypt from 'bcryptjs';
import * as jwt from 'jsonwebtoken';
import { DataSource, IsNull, LessThan, Repository } from 'typeorm';
import { config } from '../config';
import { AuthUser } from '../common/decorators';
import { Errors } from '../common/errors';
import { MailService } from '../mail/mail.service';
import { passwordChangedEmail, passwordResetEmail, welcomeEmail } from '../mail/templates';
import { PasswordResetToken, UsageEvent, User } from '../entities';
import {
  buildResetUrl,
  generateResetToken,
  hashResetToken,
  isResetTokenUsable,
  isSessionRevoked,
  resetTokenExpiry,
} from './tokens';

const BCRYPT_ROUNDS = 12;
/** Compared against when the account does not exist, so timing does not reveal it. */
const DUMMY_HASH = '$2a$12$invalidinvalidinvalidinvalidinvalidinvalidinvalidinva';

export interface AuthSession extends AuthUser {
  /** Issue time in epoch milliseconds: the `iat_ms` claim, or `iat` × 1000 for tokens without it. */
  issuedAtMs: number;
}

@Injectable()
export class AuthService {
  private readonly logger = new Logger(AuthService.name);

  /**
   * `password_changed_at` per user, so the guard does not read the database on
   * every request when caching is enabled. At the default TTL of 0 the check is
   * exact and every session revoked elsewhere stops working immediately.
   */
  private readonly revocationCache = new Map<string, { value: Date | null; expiresAt: number }>();

  constructor(
    @InjectRepository(User) private readonly users: Repository<User>,
    @InjectRepository(UsageEvent) private readonly usage: Repository<UsageEvent>,
    @InjectRepository(PasswordResetToken) private readonly resetTokens: Repository<PasswordResetToken>,
    private readonly dataSource: DataSource,
    private readonly mail: MailService,
  ) {}

  async register(emailRaw: string, password: string, displayName?: string) {
    const email = emailRaw.trim().toLowerCase();
    if (await this.users.findOne({ where: { email } })) throw Errors.emailTaken();
    const passwordHash = await bcrypt.hash(password, BCRYPT_ROUNDS);
    const user = await this.users.save(
      this.users.create({ email, passwordHash, displayName: displayName?.trim() || null }),
    );
    // Never let a mail outage block sign-up.
    void this.mail.sendQuietly(
      {
        to: { email: user.email, name: user.displayName },
        ...welcomeEmail({
          displayName: user.displayName,
          appUrl: new URL('/dashboard', config.publicWebUrl).toString(),
        }),
      },
      { userId: user.user_id },
    );
    return user;
  }

  async login(emailRaw: string, password: string) {
    const email = emailRaw.trim().toLowerCase();
    const user = await this.users.findOne({ where: { email } });
    // Compare even when the user is missing so timing does not reveal account existence.
    const ok = await bcrypt.compare(password, user?.passwordHash ?? DUMMY_HASH);
    if (!user || !ok) throw Errors.invalidCredentials();
    return user;
  }

  sign(user: { user_id: string; email: string }): string {
    // `iat` is whole seconds; `iat_ms` lets revocation compare at millisecond precision.
    return jwt.sign({ sub: user.user_id, email: user.email, iat_ms: Date.now() }, config.jwtSecret, {
      expiresIn: config.sessionTtlHours * 3600,
      issuer: 'cliprover',
    });
  }

  verify(token: string): AuthSession | null {
    try {
      const payload = jwt.verify(token, config.jwtSecret, { issuer: 'cliprover' }) as jwt.JwtPayload;
      if (typeof payload.sub !== 'string' || typeof payload.email !== 'string') return null;
      const iatMs: unknown = payload.iat_ms;
      const issuedAtMs = typeof iatMs === 'number' ? iatMs : (payload.iat ?? 0) * 1000;
      return { id: payload.sub, email: payload.email, issuedAtMs };
    } catch {
      return null;
    }
  }

  /**
   * True when the password changed after this session was issued. Fails open on
   * a database error: an infrastructure blip must not sign everyone out.
   */
  async isSessionRevoked(session: AuthSession): Promise<boolean> {
    try {
      return isSessionRevoked(session.issuedAtMs, await this.passwordChangedAt(session.id));
    } catch (err) {
      this.logger.warn({ err, userId: session.id }, 'Could not check session revocation; allowing the request');
      return false;
    }
  }

  private async passwordChangedAt(userId: string): Promise<Date | null> {
    const now = Date.now();
    const hit = this.revocationCache.get(userId);
    if (hit && hit.expiresAt > now) return hit.value;

    const row = await this.users.findOne({
      where: { user_id: userId },
      select: { user_id: true, passwordChangedAt: true },
    });
    // A deleted account has no sessions to keep alive: revoke by using "now".
    const value = row ? row.passwordChangedAt : new Date();
    this.revocationCache.set(userId, { value, expiresAt: now + config.revocationCacheSeconds * 1000 });
    if (this.revocationCache.size > 5_000) this.evictExpired(now);
    return value;
  }

  private evictExpired(now: number) {
    for (const [key, entry] of this.revocationCache) if (entry.expiresAt <= now) this.revocationCache.delete(key);
  }

  private forgetRevocation(userId: string) {
    this.revocationCache.delete(userId);
  }

  /**
   * Always succeeds from the caller's point of view, whether or not the address
   * has an account — otherwise the response enumerates registered users. Mail
   * failures are logged, never surfaced, for the same reason.
   */
  async requestPasswordReset(emailRaw: string): Promise<void> {
    const email = emailRaw.trim().toLowerCase();
    const user = await this.users.findOne({ where: { email } });
    if (!user) {
      this.logger.log({ email }, 'Password reset requested for an address with no account');
      return;
    }

    const { token, tokenHash } = generateResetToken();
    const expiresAt = resetTokenExpiry(config.passwordResetTtlMinutes);
    await this.dataSource.transaction(async (tx) => {
      // Only the newest link stays live.
      await tx
        .getRepository(PasswordResetToken)
        .update({ user_id: user.user_id, usedAt: IsNull() }, { usedAt: new Date() });
      await tx.getRepository(PasswordResetToken).insert({ user_id: user.user_id, tokenHash, expiresAt });
    });

    await this.mail.sendQuietly(
      {
        to: { email: user.email, name: user.displayName },
        ...passwordResetEmail({
          displayName: user.displayName,
          resetUrl: buildResetUrl(config.publicWebUrl, token),
          ttlMinutes: config.passwordResetTtlMinutes,
        }),
      },
      { userId: user.user_id },
    );
  }

  /** Redeems a reset link and returns the user, who is then signed in. */
  async resetPassword(token: string, newPassword: string): Promise<User> {
    const row = await this.resetTokens.findOne({ where: { tokenHash: hashResetToken(token) } });
    if (!isResetTokenUsable(row)) throw Errors.invalidResetToken();

    const user = await this.users.findOne({ where: { user_id: row!.user_id } });
    if (!user) throw Errors.invalidResetToken();

    await this.applyNewPassword(user, newPassword, row!.password_reset_token_id);
    await this.notifyPasswordChanged(user);
    return user;
  }

  async changePassword(userId: string, currentPassword: string, newPassword: string): Promise<User> {
    const user = await this.users.findOne({ where: { user_id: userId } });
    if (!user) throw Errors.unauthorized();
    if (!(await bcrypt.compare(currentPassword, user.passwordHash))) throw Errors.wrongPassword();
    if (await bcrypt.compare(newPassword, user.passwordHash)) throw Errors.passwordUnchanged();

    await this.applyNewPassword(user, newPassword, null);
    await this.notifyPasswordChanged(user);
    return user;
  }

  /**
   * One transaction: new hash, a revocation point that invalidates every session
   * issued earlier, the redeemed token marked used, and any other outstanding
   * reset links killed.
   */
  private async applyNewPassword(user: User, newPassword: string, redeemedTokenId: string | null): Promise<void> {
    const passwordHash = await bcrypt.hash(newPassword, BCRYPT_ROUNDS);
    const now = new Date();

    await this.dataSource.transaction(async (tx) => {
      await tx.getRepository(User).update({ user_id: user.user_id }, { passwordHash, passwordChangedAt: now });
      if (redeemedTokenId) {
        await tx.getRepository(PasswordResetToken).update({ password_reset_token_id: redeemedTokenId }, { usedAt: now });
      }
      await tx.getRepository(PasswordResetToken).update({ user_id: user.user_id, usedAt: IsNull() }, { usedAt: now });
    });

    user.passwordHash = passwordHash;
    user.passwordChangedAt = now;
    this.forgetRevocation(user.user_id);
  }

  private async notifyPasswordChanged(user: User): Promise<void> {
    await this.mail.sendQuietly(
      {
        to: { email: user.email, name: user.displayName },
        ...passwordChangedEmail({
          displayName: user.displayName,
          supportUrl: new URL('/forgot-password', config.publicWebUrl).toString(),
        }),
      },
      { userId: user.user_id },
    );
  }

  /** Housekeeping for expired reset rows; safe to call repeatedly. */
  async purgeExpiredResetTokens(now: Date = new Date()): Promise<number> {
    const res = await this.resetTokens.delete({ expiresAt: LessThan(now) });
    return res.affected ?? 0;
  }

  async profile(userId: string) {
    const user = await this.users.findOne({ where: { user_id: userId } });
    if (!user) throw Errors.unauthorized();
    const rows = await this.usage
      .createQueryBuilder('u')
      .select('u.event_type', 'eventType')
      .addSelect('SUM(u.units)', 'total')
      .where('u.user_id = :userId', { userId })
      .groupBy('u.event_type')
      .getRawMany<{ eventType: string; total: string }>();
    const sum = (t: string) => Number(rows.find((r) => r.eventType === t)?.total ?? 0);
    return {
      id: user.user_id,
      email: user.email,
      displayName: user.displayName,
      plan: user.plan,
      createdAt: user.createdAt,
      usage: {
        transcribedMinutes: Math.round(sum('transcription.minutes') * 10) / 10,
        renderedSeconds: Math.round(sum('render.seconds')),
      },
    };
  }
}
