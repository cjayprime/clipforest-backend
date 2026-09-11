import { Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import * as bcrypt from 'bcryptjs';
import * as jwt from 'jsonwebtoken';
import { Repository } from 'typeorm';
import { config } from '../config';
import { AuthUser } from '../common/decorators';
import { Errors } from '../common/errors';
import { UsageEvent, User } from '../entities';

@Injectable()
export class AuthService {
  constructor(
    @InjectRepository(User) private readonly users: Repository<User>,
    @InjectRepository(UsageEvent) private readonly usage: Repository<UsageEvent>,
  ) {}

  async register(emailRaw: string, password: string, displayName?: string) {
    const email = emailRaw.trim().toLowerCase();
    if (await this.users.findOne({ where: { email } })) throw Errors.emailTaken();
    const passwordHash = await bcrypt.hash(password, 12);
    return this.users.save(this.users.create({ email, passwordHash, displayName: displayName?.trim() || null }));
  }

  async login(emailRaw: string, password: string) {
    const email = emailRaw.trim().toLowerCase();
    const user = await this.users.findOne({ where: { email } });
    // Compare even when the user is missing so timing does not reveal account existence.
    const ok = await bcrypt.compare(password, user?.passwordHash ?? '$2a$12$invalidinvalidinvalidinvalidinvalidinvalidinvalidinva');
    if (!user || !ok) throw Errors.invalidCredentials();
    return user;
  }

  sign(user: { id: string; email: string }): string {
    return jwt.sign({ sub: user.id, email: user.email }, config.jwtSecret, {
      expiresIn: config.sessionTtlHours * 3600,
      issuer: 'clipforest',
    });
  }

  verify(token: string): AuthUser | null {
    try {
      const payload = jwt.verify(token, config.jwtSecret, { issuer: 'clipforest' }) as jwt.JwtPayload;
      if (typeof payload.sub !== 'string' || typeof payload.email !== 'string') return null;
      return { id: payload.sub, email: payload.email };
    } catch {
      return null;
    }
  }

  async profile(userId: string) {
    const user = await this.users.findOne({ where: { id: userId } });
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
      id: user.id,
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
