/**
 * Transactional email content: each template returns subject, HTML and plain
 * text. The HTML uses tables, inline styles and no web fonts for compatibility
 * with Outlook and Gmail.
 */
import { config } from '../config';

/** Light and high-contrast: dark backgrounds render unpredictably across mail clients. */
const BRAND = {
  accent: '#4c6b1f',
  accentContrast: '#ffffff',
  ink: '#1b211c',
  muted: '#5c6a5f',
  hairline: '#dfe4dc',
  canvas: '#f4f6f2',
};

/** The plain-text part is not optional: mail with no text alternative is far more likely to be filed as spam. */
export interface EmailContent {
  subject: string;
  html: string;
  text: string;
  tags?: string[];
}

/** Escapes text interpolated into HTML. Display names come from user input. */
export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function layout(opts: { preheader: string; heading: string; body: string; footer?: string }): string {
  const year = new Date().getFullYear();
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>${escapeHtml(opts.heading)}</title>
</head>
<body style="margin:0;padding:0;background:${BRAND.canvas};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">${escapeHtml(opts.preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:${BRAND.canvas};padding:24px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border:1px solid ${BRAND.hairline};border-radius:14px;">
<tr><td style="padding:28px 32px 8px 32px;font-family:Arial,Helvetica,sans-serif;">
<div style="font-size:17px;font-weight:bold;color:${BRAND.ink};letter-spacing:-0.2px;">${escapeHtml(config.appName)}</div>
</td></tr>
<tr><td style="padding:8px 32px 0 32px;font-family:Arial,Helvetica,sans-serif;">
<h1 style="margin:0 0 14px 0;font-size:22px;line-height:1.3;color:${BRAND.ink};font-weight:bold;">${escapeHtml(opts.heading)}</h1>
</td></tr>
<tr><td style="padding:0 32px 24px 32px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;color:${BRAND.muted};">
${opts.body}
</td></tr>
<tr><td style="padding:0 32px 28px 32px;font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:1.6;color:${BRAND.muted};border-top:1px solid ${BRAND.hairline};padding-top:18px;">
${opts.footer ?? `You are receiving this because someone used this address on ${escapeHtml(config.appName)}.`}
<br>&copy; ${year} ${escapeHtml(config.appName)}
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>`;
}

function button(href: string, label: string): string {
  return `<table role="presentation" cellpadding="0" cellspacing="0" style="margin:22px 0;"><tr>
<td style="border-radius:10px;background:${BRAND.accent};">
<a href="${escapeHtml(href)}" style="display:inline-block;padding:13px 26px;font-family:Arial,Helvetica,sans-serif;font-size:15px;font-weight:bold;color:${BRAND.accentContrast};text-decoration:none;border-radius:10px;">${escapeHtml(label)}</a>
</td></tr></table>`;
}

const greeting = (name: string | null) => (name ? `Hi ${escapeHtml(name)},` : 'Hi,');
const greetingText = (name: string | null) => (name ? `Hi ${name},` : 'Hi,');

export function welcomeEmail(opts: { displayName: string | null; appUrl: string }): EmailContent {
  const { displayName, appUrl } = opts;
  return {
    subject: `Welcome to ${config.appName}`,
    tags: ['welcome'],
    html: layout({
      preheader: 'Upload a video and get your first ranked clips.',
      heading: `Welcome to ${config.appName}`,
      body: `<p style="margin:0 0 12px 0;">${greeting(displayName)}</p>
<p style="margin:0 0 12px 0;">Your account is ready. Upload a long video and you will get back a ranked shortlist of the moments worth clipping, each one renderable as a captioned short.</p>
${button(appUrl, 'Upload your first video')}`,
    }),
    text: `${greetingText(displayName)}

Your account is ready. Upload a long video and get back a ranked shortlist of moments worth clipping.

${appUrl}`,
  };
}

export function passwordResetEmail(opts: { displayName: string | null; resetUrl: string; ttlMinutes: number }): EmailContent {
  const { displayName, resetUrl, ttlMinutes } = opts;
  const expiry = ttlMinutes >= 60 ? `${Math.round(ttlMinutes / 60)} hour(s)` : `${ttlMinutes} minutes`;
  return {
    subject: `Reset your ${config.appName} password`,
    tags: ['password-reset'],
    html: layout({
      preheader: `This link expires in ${expiry}.`,
      heading: 'Reset your password',
      body: `<p style="margin:0 0 12px 0;">${greeting(displayName)}</p>
<p style="margin:0 0 12px 0;">We received a request to reset the password for your ${escapeHtml(config.appName)} account. Choose a new password with the button below.</p>
${button(resetUrl, 'Choose a new password')}
<p style="margin:0 0 12px 0;">This link works once and expires in <strong>${escapeHtml(expiry)}</strong>.</p>
<p style="margin:0 0 12px 0;">If you did not ask for this, you can ignore this email — your password will not change.</p>
<p style="margin:16px 0 0 0;font-size:13px;color:${BRAND.muted};">If the button does not work, paste this into your browser:<br>
<span style="word-break:break-all;">${escapeHtml(resetUrl)}</span></p>`,
    }),
    text: `${greetingText(displayName)}

We received a request to reset the password for your ${config.appName} account.

Choose a new password:
${resetUrl}

This link works once and expires in ${expiry}.
If you did not ask for this, ignore this email — your password will not change.`,
  };
}

export function passwordChangedEmail(opts: { displayName: string | null; supportUrl: string }): EmailContent {
  const { displayName, supportUrl } = opts;
  return {
    subject: `Your ${config.appName} password was changed`,
    tags: ['password-changed'],
    html: layout({
      preheader: 'If this was not you, act now.',
      heading: 'Your password was changed',
      body: `<p style="margin:0 0 12px 0;">${greeting(displayName)}</p>
<p style="margin:0 0 12px 0;">The password on your ${escapeHtml(config.appName)} account was just changed, and every other signed-in device has been signed out.</p>
<p style="margin:0 0 12px 0;"><strong>If this was not you</strong>, reset your password immediately to take the account back.</p>
${button(supportUrl, 'Reset your password')}`,
    }),
    text: `${greetingText(displayName)}

The password on your ${config.appName} account was just changed, and every other signed-in device has been signed out.

If this was not you, reset your password immediately:
${supportUrl}`,
  };
}
