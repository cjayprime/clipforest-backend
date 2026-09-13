import { Column, CreateDateColumn, Entity, Index, JoinColumn, ManyToOne, PrimaryGeneratedColumn } from 'typeorm';
import { User } from './user.entity';

/**
 * Password reset links. Only the SHA-256 of the token is stored, so a database
 * leak cannot be replayed as a reset; the plaintext exists only in the email.
 */
@Index('password_reset_tokens_user_id_idx', ['user_id'])
@Entity('password_reset_tokens')
export class PasswordResetToken {
  @PrimaryGeneratedColumn('increment', { type: 'bigint' })
  password_reset_token_id: string;

  @Column({ type: 'bigint' })
  user_id: string;

  @Column({ name: 'token_hash', unique: true })
  tokenHash: string;

  @Column({ name: 'expires_at', type: 'timestamptz' })
  expiresAt: Date;

  /** Single use: set the moment the token is redeemed. */
  @Column({ name: 'used_at', type: 'timestamptz', nullable: true })
  usedAt: Date | null;

  @CreateDateColumn({ name: 'created_at', type: 'timestamptz' })
  createdAt: Date;

  @ManyToOne(() => User, { onDelete: 'CASCADE' })
  @JoinColumn({ name: 'user_id' })
  user: User;
}
