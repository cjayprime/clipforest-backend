import { Column, CreateDateColumn, Entity, OneToMany, PrimaryGeneratedColumn, UpdateDateColumn } from 'typeorm';
import { Video } from './video.entity';

@Entity('users')
export class User {
  // bigint: TypeORM surfaces it as a string.
  @PrimaryGeneratedColumn('increment', { type: 'bigint' })
  user_id: string;

  @Column({ unique: true })
  email: string;

  @Column({ name: 'password_hash' })
  passwordHash: string;

  @Column({ name: 'display_name', type: 'text', nullable: true })
  displayName: string | null;

  @Column({ default: 'free' })
  plan: string;

  @Column({ type: 'jsonb', nullable: true })
  limits: Record<string, unknown> | null;

  /**
   * Set whenever the password changes. Sessions issued before this instant are
   * refused, so a reset or change signs the account out everywhere else.
   * NULL for accounts whose password has never changed.
   */
  @Column({ name: 'password_changed_at', type: 'timestamptz', nullable: true })
  passwordChangedAt: Date | null;

  @CreateDateColumn({ name: 'created_at', type: 'timestamptz' })
  createdAt: Date;

  @UpdateDateColumn({ name: 'updated_at', type: 'timestamptz' })
  updatedAt: Date;

  @OneToMany(() => Video, (v) => v.user)
  videos: Video[];
}
