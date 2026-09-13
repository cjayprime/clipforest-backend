import { createHmac } from 'node:crypto';
import { addCalendarMonths, dueAnnualMonths } from '../../api/src/billing/annual-allowance.scheduler';
import { applyGrant, processingCost, splitGrant, totalCredits, type Balance } from '../../api/src/billing/credits';
import { PLANS } from '../../api/src/billing/plans';
import { planForProduct, productForPlan, purchasableIntervals } from '../../api/src/billing/polar/product-plan-map';
import { config } from '../../api/src/config';
import { verifyWebhookSignature, webhookSigningKeys } from '../../api/src/billing/polar/verify-webhook';

const MODERN_SECRET = `whsec_${Buffer.from('a-32-byte-signing-key-for-tests!').toString('base64')}`;
const LEGACY_SECRET = 'whsec_legacy_polar_hmac_secret_value';

function sign(secret: string, id: string, timestamp: string, body: string, encoding: 'modern' | 'legacy'): string {
  const key =
    encoding === 'modern'
      ? Buffer.from(secret.slice('whsec_'.length), 'base64')
      : Buffer.from(secret, 'utf8');
  return `v1,${createHmac('sha256', key).update(`${id}.${timestamp}.${body}`, 'utf8').digest('base64')}`;
}

describe('polar webhook signatures', () => {
  const now = new Date('2026-09-13T12:00:00.000Z');
  const timestamp = String(Math.floor(now.getTime() / 1000));
  const id = 'msg_2abc';
  const body = JSON.stringify({ type: 'order.paid', data: { id: 'ord_1' } });

  it('accepts a signature made with the modern base64 secret', () => {
    expect(
      verifyWebhookSignature({
        secret: MODERN_SECRET,
        headers: { id, timestamp, signature: sign(MODERN_SECRET, id, timestamp, body, 'modern') },
        body,
        now,
      }),
    ).toBe(true);
  });

  it('accepts a signature made with the legacy whole-string secret', () => {
    // Secrets issued before 8 Sep 2026 key the HMAC with the UTF-8 bytes of the
    // entire `whsec_…` string. Both vintages must verify.
    expect(
      verifyWebhookSignature({
        secret: LEGACY_SECRET,
        headers: { id, timestamp, signature: sign(LEGACY_SECRET, id, timestamp, body, 'legacy') },
        body,
        now,
      }),
    ).toBe(true);
  });

  it('offers both signing keys, newest convention first', () => {
    const keys = webhookSigningKeys(MODERN_SECRET);
    expect(keys).toHaveLength(2);
    expect(keys[0].equals(Buffer.from(MODERN_SECRET.slice(6), 'base64'))).toBe(true);
    expect(keys[1].equals(Buffer.from(MODERN_SECRET, 'utf8'))).toBe(true);
  });

  it('rejects a tampered body', () => {
    const signature = sign(MODERN_SECRET, id, timestamp, body, 'modern');
    expect(
      verifyWebhookSignature({
        secret: MODERN_SECRET,
        headers: { id, timestamp, signature },
        body: body.replace('ord_1', 'ord_999'),
        now,
      }),
    ).toBe(false);
  });

  it('rejects a signature bound to a different id or timestamp', () => {
    const signature = sign(MODERN_SECRET, id, timestamp, body, 'modern');
    expect(verifyWebhookSignature({ secret: MODERN_SECRET, headers: { id: 'msg_other', timestamp, signature }, body, now })).toBe(false);
    expect(
      verifyWebhookSignature({ secret: MODERN_SECRET, headers: { id, timestamp: String(Number(timestamp) - 1), signature }, body, now }),
    ).toBe(false);
  });

  it('rejects replays outside the tolerance window, in both directions', () => {
    const old = String(Math.floor(now.getTime() / 1000) - 3600);
    const future = String(Math.floor(now.getTime() / 1000) + 3600);
    for (const ts of [old, future]) {
      expect(
        verifyWebhookSignature({
          secret: MODERN_SECRET,
          headers: { id, timestamp: ts, signature: sign(MODERN_SECRET, id, ts, body, 'modern') },
          body,
          now,
        }),
      ).toBe(false);
    }
  });

  it('accepts when one of several offered signatures matches (key rotation)', () => {
    const good = sign(MODERN_SECRET, id, timestamp, body, 'modern');
    expect(
      verifyWebhookSignature({ secret: MODERN_SECRET, headers: { id, timestamp, signature: `v1,AAAA ${good}` }, body, now }),
    ).toBe(true);
  });

  it('refuses anything missing', () => {
    const signature = sign(MODERN_SECRET, id, timestamp, body, 'modern');
    expect(verifyWebhookSignature({ secret: '', headers: { id, timestamp, signature }, body, now })).toBe(false);
    expect(verifyWebhookSignature({ secret: MODERN_SECRET, headers: { id: undefined, timestamp, signature }, body, now })).toBe(false);
    expect(verifyWebhookSignature({ secret: MODERN_SECRET, headers: { id, timestamp, signature: undefined }, body, now })).toBe(false);
    expect(verifyWebhookSignature({ secret: MODERN_SECRET, headers: { id, timestamp: 'not-a-number', signature }, body, now })).toBe(false);
  });
});

describe('credit split and rollover', () => {
  it('splits a grant 90/10 by default, never minting a credit', () => {
    expect(splitGrant(100)).toEqual({ rollover: 90, expiring: 10 });
    expect(splitGrant(300)).toEqual({ rollover: 270, expiring: 30 });
    // The remainder always lands in the expiring bucket, so the parts sum exactly.
    const odd = splitGrant(101);
    expect(odd.rollover + odd.expiring).toBe(101);
  });

  it('accumulates rollover across months but replaces the expiring part', () => {
    let balance: Balance = { rollover: 0, expiring: 0 };
    balance = applyGrant(balance, PLANS.starter.monthlyCredits);
    expect(balance).toEqual({ rollover: 90, expiring: 10 });

    // A second untouched month: rollover stacks, the expiring 10 is lost.
    balance = applyGrant(balance, PLANS.starter.monthlyCredits);
    expect(balance).toEqual({ rollover: 180, expiring: 10 });
    expect(totalCredits(balance)).toBe(190);
  });

  it('honours a configured rollover share', () => {
    expect(splitGrant(100, 1)).toEqual({ rollover: 100, expiring: 0 });
    expect(splitGrant(100, 0)).toEqual({ rollover: 0, expiring: 100 });
    expect(() => splitGrant(100, 1.5)).toThrow(RangeError);
    expect(() => splitGrant(-1)).toThrow(RangeError);
  });

  it('matches the plans advertised on the pricing page', () => {
    expect(PLANS.starter.monthlyCredits).toBe(100);
    expect(PLANS.creator.monthlyCredits).toBe(300);
    expect(PLANS.studio.monthlyCredits).toBe(1000);
  });
});

// ------------------------------------------------------ processing cost
const CASES: [durationMs: number, perMinute: number, expected: number][] = [
  [0, 1, 0],
  [1, 1, 1],
  [60_000, 1, 1],
  [60_001, 1, 2],
  [150_000, 2, 6],
  [100_000, 60, 120],
  [10_800_000, 1, 180],
  [-5, 1, 0],
  [60_000, 0, 0],
];

describe('processingCost', () => {
  it.each(CASES)('%i ms at %i credits/minute costs %i', (durationMs, perMinute, expected) => {
    expect(processingCost(durationMs, perMinute)).toBe(expected);
  });
});

// ------------------------------------------------------ Polar products
type Products = typeof config.polar.products;

const ALL: Products = {
  starter: { month: 'prod_starter_m', year: 'prod_starter_y' },
  creator: { month: 'prod_creator_m', year: 'prod_creator_y' },
  studio: { month: 'prod_studio_m', year: 'prod_studio_y' },
};

function useProducts(products: Products) {
  jest.replaceProperty(config.polar, 'products', products);
}

afterEach(() => {
  jest.restoreAllMocks();
});

describe('planForProduct', () => {
  it('maps monthly and annual products back to their plan and interval', () => {
    useProducts(ALL);
    expect(planForProduct('prod_creator_m')).toEqual({ plan: 'creator', interval: 'month' });
    expect(planForProduct('prod_studio_y')).toEqual({ plan: 'studio', interval: 'year' });
  });

  it('never turns an unknown or missing product into a plan', () => {
    useProducts(ALL);
    expect(planForProduct('prod_somebody_else')).toBeNull();
    expect(planForProduct('')).toBeNull();
    expect(planForProduct(null)).toBeNull();
    expect(planForProduct(undefined)).toBeNull();
  });

  it('does not match an unconfigured interval against an empty id', () => {
    useProducts({ ...ALL, starter: { month: 'prod_starter_m', year: undefined } });
    expect(planForProduct(undefined)).toBeNull();
    expect(planForProduct('prod_starter_m')).toEqual({ plan: 'starter', interval: 'month' });
  });
});

describe('productForPlan', () => {
  it('finds the product for a plan and interval, and nothing for free or unconfigured', () => {
    useProducts({ ...ALL, creator: { month: 'prod_creator_m', year: undefined } });
    expect(productForPlan('starter', 'year')).toBe('prod_starter_y');
    expect(productForPlan('creator', 'year')).toBeNull();
    expect(productForPlan('free', 'month')).toBeNull();
  });
});

describe('purchasableIntervals', () => {
  it('offers both intervals when every plan has both', () => {
    useProducts(ALL);
    expect(purchasableIntervals()).toEqual(['month', 'year']);
  });

  it('withholds an interval that even one plan lacks, so the pricing toggle cannot lead to an error', () => {
    useProducts({ ...ALL, studio: { month: 'prod_studio_m', year: undefined } });
    expect(purchasableIntervals()).toEqual(['month']);
  });

  it('offers nothing before products are configured', () => {
    useProducts({ starter: { month: undefined, year: undefined }, creator: { month: undefined, year: undefined }, studio: { month: undefined, year: undefined } });
    expect(purchasableIntervals()).toEqual([]);
  });
});

// ------------------------------------------------------ annual allowance
const at = (iso: string) => new Date(iso);

describe('addCalendarMonths', () => {
  it('clamps to the last day of a shorter month, including leap years', () => {
    expect(addCalendarMonths(at('2027-01-31T10:00:00.000Z'), 1).toISOString()).toBe('2027-02-28T10:00:00.000Z');
    expect(addCalendarMonths(at('2028-01-31T10:00:00.000Z'), 1).toISOString()).toBe('2028-02-29T10:00:00.000Z');
  });

  it('measures every month from the original date, so anniversaries never drift', () => {
    // Chaining through February would land on 28 March; the 31st must survive.
    expect(addCalendarMonths(at('2027-01-31T00:00:00.000Z'), 2).toISOString()).toBe('2027-03-31T00:00:00.000Z');
  });

  it('rolls into the next year and keeps the time of day', () => {
    expect(addCalendarMonths(at('2026-12-15T23:59:59.123Z'), 1).toISOString()).toBe('2027-01-15T23:59:59.123Z');
  });
});

describe('dueAnnualMonths', () => {
  const start = at('2026-01-15T12:00:00.000Z');
  const end = at('2027-01-15T12:00:00.000Z');

  it('owes nothing before the first anniversary', () => {
    expect(dueAnnualMonths(start, end, at('2026-02-15T11:59:59.999Z'))).toEqual([]);
  });

  it('owes a month from the moment its anniversary passes', () => {
    expect(dueAnnualMonths(start, end, at('2026-02-15T12:00:00.000Z'))).toEqual([1]);
  });

  it('returns every month owed, so a job that was down catches up', () => {
    expect(dueAnnualMonths(start, end, at('2026-05-20T00:00:00.000Z'))).toEqual([1, 2, 3, 4]);
  });

  it('never returns month 0, which the paid order grants, or a twelfth top-up', () => {
    expect(dueAnnualMonths(start, end, at('2027-06-01T00:00:00.000Z'))).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
  });

  it('stops at the end of the paid period', () => {
    expect(dueAnnualMonths(start, at('2026-04-15T12:00:00.000Z'), at('2026-09-01T00:00:00.000Z'))).toEqual([1, 2]);
  });
});
