/** Transactional email through a configured provider (Brevo, console or noop). */
import { Injectable, Logger } from '@nestjs/common';
import { config } from '../config';
import { Errors } from '../common/errors';
import type { EmailContent } from './templates';

export interface MailAddress {
  email: string;
  name?: string | null;
}

/** Rendered content plus who it goes to — what MailService actually sends. */
export interface MailMessage extends EmailContent {
  to: MailAddress | string;
  replyTo?: MailAddress;
}

/**
 * brevo   — Brevo's transactional API
 * console — logs the rendered email, including links; the default with no API
 *           key, so the whole flow is exercisable offline
 * noop    — accepts and discards, for tests
 */
export type MailProviderName = 'brevo' | 'console' | 'noop';

export interface MailResult {
  provider: MailProviderName;
  messageId: string | null;
  delivered: boolean;
}

/** A message held by the dev inbox (MAIL_TEST_INBOX), never in production. */
export interface SentMail {
  to: string;
  subject: string;
  text: string;
  tags: string[];
  at: string;
}

/**
 * Chosen once at boot. `auto` means Brevo when an API key is present and the
 * console provider otherwise, so the app is always able to "send".
 */
export function resolveMailProvider(
  configured: string = config.mail.provider,
  hasBrevoKey = Boolean(config.mail.brevoApiKey),
): MailProviderName {
  const choice = configured.trim().toLowerCase();
  if (choice === 'brevo' || choice === 'console' || choice === 'noop') return choice;
  return hasBrevoKey ? 'brevo' : 'console';
}

const BREVO_ENDPOINT = 'https://api.brevo.com/v3/smtp/email';
/** Brevo rejects display names longer than this. */
const MAX_NAME = 70;
const TEST_INBOX_LIMIT = 20;

function toAddress(to: MailAddress | string): MailAddress {
  return typeof to === 'string' ? { email: to } : to;
}

function trimName(name: string | null | undefined): string | undefined {
  const v = (name ?? '').trim();
  return v ? v.slice(0, MAX_NAME) : undefined;
}

@Injectable()
export class MailService {
  private readonly logger = new Logger(MailService.name);
  readonly provider: MailProviderName;

  /** Most recent messages, only when the dev inbox is enabled. */
  private readonly recent: SentMail[] = [];

  constructor() {
    this.provider = resolveMailProvider();
    if (this.provider === 'brevo') {
      this.logger.log(`Sending email via Brevo as ${config.mail.fromEmail}`);
    } else if (this.provider === 'console') {
      this.logger.warn(
        'No BREVO_API_KEY set — emails will be printed to the log instead of sent. Password reset links will appear here.',
      );
    }
  }

  get testInboxEnabled(): boolean {
    return config.mail.testInbox;
  }

  /** Newest first. Empty unless MAIL_TEST_INBOX is on. */
  inbox(): SentMail[] {
    return [...this.recent].reverse();
  }

  private record(message: MailMessage, to: MailAddress) {
    if (!config.mail.testInbox) return;
    this.recent.push({
      to: to.email,
      subject: message.subject,
      text: message.text,
      tags: message.tags ?? [],
      at: new Date().toISOString(),
    });
    if (this.recent.length > TEST_INBOX_LIMIT) this.recent.shift();
  }

  /** Throws on failure. Use `sendQuietly` when delivery must not fail the request. */
  async send(message: MailMessage): Promise<MailResult> {
    const to = toAddress(message.to);
    this.record(message, to);
    switch (this.provider) {
      case 'noop':
        return { provider: 'noop', messageId: null, delivered: false };
      case 'console':
        this.logger.log(
          `\n──────── email (${message.tags?.join(', ') ?? 'untagged'}) ────────\n` +
            `To:      ${to.email}\n` +
            `From:    ${config.mail.fromName} <${config.mail.fromEmail}>\n` +
            `Subject: ${message.subject}\n\n` +
            `${message.text}\n` +
            `────────────────────────────────────────`,
        );
        return { provider: 'console', messageId: null, delivered: true };
      case 'brevo':
        return this.sendViaBrevo(message, to);
    }
  }

  /**
   * Best-effort delivery: logs and swallows every failure. Used where the user's
   * action has already succeeded durably and the email is a notification, or
   * where reporting failure would leak whether an account exists.
   */
  async sendQuietly(message: MailMessage, context: Record<string, unknown> = {}): Promise<MailResult | null> {
    try {
      return await this.send(message);
    } catch (err) {
      this.logger.error(
        { err, ...context, subject: message.subject, provider: this.provider },
        'Email delivery failed',
      );
      return null;
    }
  }

  private async sendViaBrevo(message: MailMessage, to: MailAddress): Promise<MailResult> {
    const replyTo = message.replyTo ?? (config.mail.replyTo ? { email: config.mail.replyTo } : undefined);
    const payload = {
      sender: { email: config.mail.fromEmail, name: trimName(config.mail.fromName) },
      to: [{ email: to.email, ...(trimName(to.name) ? { name: trimName(to.name) } : {}) }],
      subject: message.subject,
      htmlContent: message.html,
      textContent: message.text,
      ...(message.tags?.length ? { tags: message.tags } : {}),
      ...(replyTo ? { replyTo: { email: replyTo.email, ...(trimName(replyTo.name) ? { name: trimName(replyTo.name) } : {}) } } : {}),
    };

    let res: Response;
    try {
      res = await fetch(BREVO_ENDPOINT, {
        method: 'POST',
        headers: {
          'api-key': config.mail.brevoApiKey ?? '',
          'content-type': 'application/json',
          accept: 'application/json',
        },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(config.mail.timeoutMs),
      });
    } catch (err) {
      // Network failure or timeout — transient by nature.
      this.logger.error({ err }, 'Brevo request failed');
      throw Errors.mailUnavailable();
    }

    const body = await res.text();
    if (!res.ok) {
      // Brevo answers {code, message}; never log the body verbatim, it echoes the recipient.
      let code = `HTTP_${res.status}`;
      try {
        const parsed = JSON.parse(body) as { code?: string };
        if (parsed.code) code = parsed.code;
      } catch {
        /* non-JSON error body */
      }
      this.logger.error({ status: res.status, code }, 'Brevo rejected the message');
      throw Errors.mailUnavailable();
    }

    let messageId: string | null = null;
    try {
      messageId = (JSON.parse(body) as { messageId?: string }).messageId ?? null;
    } catch {
      /* 201 with an empty body is acceptable */
    }
    return { provider: 'brevo', messageId, delivered: true };
  }
}
