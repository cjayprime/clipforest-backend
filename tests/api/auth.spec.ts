import {
  buildResetUrl,
  generateResetToken,
  hashResetToken,
  isResetTokenUsable,
  isSessionRevoked,
  RESET_TOKEN_PATTERN,
  resetTokenExpiry,
} from '../../api/src/auth/tokens';
import { resolveMailProvider } from '../../api/src/mail/mail.service';
import { escapeHtml, passwordChangedEmail, passwordResetEmail, welcomeEmail } from '../../api/src/mail/templates';

describe('password reset tokens', () => {
  it('issues URL-safe tokens and stores only their hash', () => {
    const { token, tokenHash } = generateResetToken();
    expect(token).toMatch(RESET_TOKEN_PATTERN);
    expect(encodeURIComponent(token)).toBe(token);
    expect(tokenHash).toBe(hashResetToken(token));
    // The stored value must not be reversible to, or equal to, the secret.
    expect(tokenHash).not.toBe(token);
    expect(tokenHash).toMatch(/^[0-9a-f]{64}$/);
  });

  it('never repeats a token', () => {
    const seen = new Set(Array.from({ length: 200 }, () => generateResetToken().token));
    expect(seen.size).toBe(200);
  });

  it('is redeemable once, before it expires', () => {
    const now = new Date('2026-09-13T12:00:00.000Z');
    const live = { expiresAt: new Date(now.getTime() + 60_000), usedAt: null };
    expect(isResetTokenUsable(live, now)).toBe(true);

    expect(isResetTokenUsable({ ...live, usedAt: now }, now)).toBe(false);
    expect(isResetTokenUsable({ expiresAt: new Date(now.getTime() - 1), usedAt: null }, now)).toBe(false);
    expect(isResetTokenUsable(null, now)).toBe(false);
    expect(isResetTokenUsable(undefined, now)).toBe(false);
  });

  it('expires exactly ttl minutes after issue', () => {
    const now = new Date('2026-09-13T12:00:00.000Z');
    expect(resetTokenExpiry(60, now).toISOString()).toBe('2026-09-13T13:00:00.000Z');
    expect(resetTokenExpiry(15, now).toISOString()).toBe('2026-09-13T12:15:00.000Z');
  });

  it('builds the reset link from the configured origin, not a request header', () => {
    const { token } = generateResetToken();
    const url = new URL(buildResetUrl('https://clips.example.com', token));
    expect(url.origin).toBe('https://clips.example.com');
    expect(url.pathname).toBe('/reset-password');
    expect(url.searchParams.get('token')).toBe(token);
  });
});

describe('session revocation', () => {
  const issuedAtMs = new Date('2026-09-13T12:00:10.500Z').getTime();

  it('leaves sessions alone when the password has never changed', () => {
    expect(isSessionRevoked(issuedAtMs, null)).toBe(false);
    expect(isSessionRevoked(issuedAtMs, undefined)).toBe(false);
  });

  it('revokes sessions issued before the password changed', () => {
    expect(isSessionRevoked(issuedAtMs, new Date('2026-09-13T12:00:11.000Z'))).toBe(true);
    expect(isSessionRevoked(issuedAtMs, new Date('2026-09-14T00:00:00.000Z'))).toBe(true);
  });

  it('revokes a session issued earlier in the same second as the change', () => {
    expect(isSessionRevoked(issuedAtMs, new Date('2026-09-13T12:00:10.900Z'))).toBe(true);
    expect(isSessionRevoked(issuedAtMs, new Date('2026-09-13T12:00:10.501Z'))).toBe(true);
  });

  it('keeps the session issued with, or after, the change', () => {
    expect(isSessionRevoked(issuedAtMs, new Date('2026-09-13T12:00:10.500Z'))).toBe(false);
    expect(isSessionRevoked(issuedAtMs, new Date('2026-09-13T12:00:10.100Z'))).toBe(false);
  });
});

describe('mail provider selection', () => {
  it('uses Brevo only when a key is present', () => {
    expect(resolveMailProvider('auto', true)).toBe('brevo');
    expect(resolveMailProvider('auto', false)).toBe('console');
  });

  it('honours an explicit choice regardless of the key', () => {
    expect(resolveMailProvider('console', true)).toBe('console');
    expect(resolveMailProvider('noop', true)).toBe('noop');
    expect(resolveMailProvider('brevo', false)).toBe('brevo');
    expect(resolveMailProvider('BREVO', true)).toBe('brevo');
  });

  it('falls back rather than failing on an unknown provider', () => {
    expect(resolveMailProvider('sendgrid', false)).toBe('console');
    expect(resolveMailProvider('', true)).toBe('brevo');
  });
});

describe('email templates', () => {
  const resetUrl = 'https://clips.example.com/reset-password?token=abc123';

  it('puts the link in both the HTML and the plain-text part', () => {
    const mail = passwordResetEmail({ displayName: 'Ada', resetUrl, ttlMinutes: 60 });
    expect(mail.subject).toMatch(/reset/i);
    expect(mail.html).toContain(resetUrl);
    expect(mail.text).toContain(resetUrl);
    // A text alternative is what keeps it out of spam folders.
    expect(mail.text.length).toBeGreaterThan(50);
    expect(mail.tags).toContain('password-reset');
  });

  it('escapes display names so a name cannot inject markup', () => {
    const mail = passwordResetEmail({
      displayName: '<img src=x onerror=alert(1)>',
      resetUrl,
      ttlMinutes: 60,
    });
    expect(mail.html).not.toContain('<img src=x');
    expect(mail.html).toContain('&lt;img src=x');
  });

  it('greets anonymously when there is no display name', () => {
    expect(passwordResetEmail({ displayName: null, resetUrl, ttlMinutes: 60 }).text.startsWith('Hi,')).toBe(true);
  });

  it('states the expiry in human terms', () => {
    expect(passwordResetEmail({ displayName: null, resetUrl, ttlMinutes: 30 }).text).toContain('30 minutes');
    expect(passwordResetEmail({ displayName: null, resetUrl, ttlMinutes: 60 }).text).toContain('1 hour');
  });

  it('tells the user what to do when a change was not theirs', () => {
    const mail = passwordChangedEmail({ displayName: 'Ada', supportUrl: 'https://clips.example.com/forgot-password' });
    expect(mail.subject).toMatch(/changed/i);
    expect(mail.text).toMatch(/if this was not you/i);
    expect(mail.html).toContain('https://clips.example.com/forgot-password');
  });

  it('sends a welcome pointing at the app', () => {
    const mail = welcomeEmail({ displayName: null, appUrl: 'https://clips.example.com/dashboard' });
    expect(mail.html).toContain('https://clips.example.com/dashboard');
    expect(mail.tags).toContain('welcome');
  });

  it('escapes every HTML-significant character', () => {
    expect(escapeHtml(`<>&"'`)).toBe('&lt;&gt;&amp;&quot;&#39;');
  });
});
