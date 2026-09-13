import { Logger } from '@nestjs/common';
import { QueryFailedError } from 'typeorm';
import { AnnualAllowanceScheduler } from '../../api/src/billing/annual-allowance.scheduler';
import { BillingService } from '../../api/src/billing/billing.service';
import type { PolarSubscription } from '../../api/src/billing/polar/polar-event';
import { config } from '../../api/src/config';
import type { EventsService } from '../../api/src/core/events.service';
import { CreditLedgerEntry, type Subscription } from '../../api/src/entities';

function uniqueViolation(): QueryFailedError {
  return new QueryFailedError('INSERT', [], Object.assign(new Error('duplicate key'), { code: '23505' }));
}

/**
 * BillingService over in-memory fakes. `order` records side effects as they
 * happen, which is what lets the tests assert that an event is only ever
 * published after its transaction has committed.
 */
function harness(opts: { keyExists?: boolean; ledgerFailure?: Error; balance?: [number, number]; existingSub?: boolean } = {}) {
  const order: string[] = [];
  const [rollover, expiring] = opts.balance ?? [0, 0];
  const balanceRow = { credit_balance_id: '1', user_id: '7', rolloverCredits: rollover, expiringCredits: expiring };

  const balances = {
    findOne: jest.fn().mockResolvedValue(balanceRow),
    insert: jest.fn(),
    update: jest.fn(() => {
      order.push('balance-written');
      return Promise.resolve();
    }),
  };
  const ledger = {
    exists: jest.fn().mockResolvedValue(opts.keyExists ?? false),
    create: jest.fn((entry: unknown) => entry),
    save: jest.fn((entry: unknown) => {
      if (opts.ledgerFailure) return Promise.reject(opts.ledgerFailure);
      order.push('ledger-written');
      return Promise.resolve(entry);
    }),
  };
  const tx = { getRepository: (entity: unknown) => (entity === CreditLedgerEntry ? ledger : balances) };
  const dataSource = {
    transaction: jest.fn(async (work: (manager: typeof tx) => Promise<unknown>) => {
      const result = await work(tx);
      order.push('committed');
      return result;
    }),
  };
  const subscriptions = {
    findOne: jest.fn().mockResolvedValue(opts.existingSub ? { subscription_id: '3' } : null),
    insert: jest.fn(() => {
      order.push('subscription-inserted');
      return Promise.resolve();
    }),
    update: jest.fn(() => {
      order.push('subscription-updated');
      return Promise.resolve();
    }),
  };
  const webhooks = { exists: jest.fn().mockResolvedValue(false), insert: jest.fn().mockResolvedValue(undefined) };
  const publish = jest.fn(() => {
    order.push('published');
    return Promise.resolve();
  });

  const service = new BillingService(
    balances as never,
    subscriptions as never,
    webhooks as never,
    dataSource as never,
    { publish } as unknown as EventsService,
  );
  return { service, order, publish, ledger, webhooks };
}

beforeEach(() => {
  jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined);
  jest.spyOn(Logger.prototype, 'debug').mockImplementation(() => undefined);
});

afterEach(() => {
  jest.restoreAllMocks();
});

describe('BillingService.grant', () => {
  it('writes the ledger and balance, commits, and only then announces the grant', async () => {
    const { service, order, publish, ledger } = harness();

    await expect(service.grant('7', 100, 'polar-order:ord_1')).resolves.toBe('granted');
    expect(order).toEqual(['ledger-written', 'balance-written', 'committed', 'published']);
    expect(publish).toHaveBeenCalledWith({ type: 'credits.updated', userId: '7', status: 'GRANTED' });
    expect(ledger.create).toHaveBeenCalledWith(
      expect.objectContaining({ idempotencyKey: 'polar-order:ord_1', rolloverDelta: 90, expiringDelta: 10 }),
    );
  });

  it('answers a repeat with a lookup: nothing written, nothing announced', async () => {
    const { service, order, publish, ledger } = harness({ keyExists: true });

    await expect(service.grant('7', 100, 'polar-order:ord_1')).resolves.toBe('duplicate');
    expect(ledger.save).not.toHaveBeenCalled();
    expect(publish).not.toHaveBeenCalled();
    expect(order).toEqual(['committed']);
  });

  it('treats losing a race on the unique key as a duplicate, and announces nothing', async () => {
    const { service, publish } = harness({ ledgerFailure: uniqueViolation() });

    await expect(service.grant('7', 100, 'polar-order:ord_1')).resolves.toBe('duplicate');
    expect(publish).not.toHaveBeenCalled();
  });

  it('rethrows any other failure without announcing credits that were never granted', async () => {
    const { service, publish } = harness({ ledgerFailure: new Error('connection lost') });

    await expect(service.grant('7', 100, 'polar-order:ord_1')).rejects.toThrow('connection lost');
    expect(publish).not.toHaveBeenCalled();
  });
});

describe('BillingService.assertCanAfford', () => {
  it('refuses nothing while charging is switched off', async () => {
    jest.replaceProperty(config.credits, 'enforced', false);
    const { service } = harness({ balance: [0, 0] });
    await expect(service.assertCanAfford('7', 500)).resolves.toBeUndefined();
  });

  it('refuses work the combined balance cannot cover', async () => {
    jest.replaceProperty(config.credits, 'enforced', true);
    const { service } = harness({ balance: [3, 2] });
    await expect(service.assertCanAfford('7', 6)).rejects.toMatchObject({ code: 'BILLING_INSUFFICIENT_CREDITS' });
  });

  it('allows work that uses the balance exactly', async () => {
    jest.replaceProperty(config.credits, 'enforced', true);
    const { service } = harness({ balance: [3, 2] });
    await expect(service.assertCanAfford('7', 5)).resolves.toBeUndefined();
  });
});

describe('BillingService.upsertSubscription', () => {
  const sub = { id: 'sub_1', status: 'canceled', customer_id: 'cus_1', product_id: 'p', recurring_interval: 'month' } as PolarSubscription;

  it('announces a new subscription after it is stored', async () => {
    const { service, order, publish } = harness();
    await service.upsertSubscription('7', 'starter', sub);
    expect(order).toEqual(['subscription-inserted', 'published']);
    expect(publish).toHaveBeenCalledWith({ type: 'subscription.updated', userId: '7', status: 'canceled' });
  });

  it('announces a change to an existing subscription after it is stored', async () => {
    const { service, order } = harness({ existingSub: true });
    await service.upsertSubscription('7', 'starter', sub);
    expect(order).toEqual(['subscription-updated', 'published']);
  });
});

describe('BillingService.claimWebhook', () => {
  it('recognises a redelivery without attempting an insert that would fail', async () => {
    const { service, webhooks } = harness();
    webhooks.exists.mockResolvedValue(true);
    await expect(service.claimWebhook('polar', 'msg_1', 'order.paid')).resolves.toBe(false);
    expect(webhooks.insert).not.toHaveBeenCalled();
  });

  it('claims a delivery it has not seen', async () => {
    const { service, webhooks } = harness();
    await expect(service.claimWebhook('polar', 'msg_1', 'order.paid')).resolves.toBe(true);
    expect(webhooks.insert).toHaveBeenCalledWith({ provider: 'polar', eventId: 'msg_1', eventType: 'order.paid' });
  });
});

const at = (iso: string) => new Date(iso);

describe('AnnualAllowanceScheduler', () => {
  const start = at('2026-01-15T12:00:00.000Z');

  function subscription(overrides: Partial<Subscription> = {}): Subscription {
    return {
      subscription_id: '1',
      user_id: '7',
      polarSubscriptionId: 'sub_1',
      plan: 'creator',
      status: 'active',
      recurringInterval: 'year',
      currentPeriodStart: start,
      currentPeriodEnd: at('2027-01-15T12:00:00.000Z'),
      ...overrides,
    } as Subscription;
  }

  function scheduler(rows: Subscription[], grant: jest.Mock) {
    const find = jest.fn().mockResolvedValue(rows);
    const job = new AnnualAllowanceScheduler({ find } as never, { grant } as unknown as BillingService);
    return { job, find };
  }

  beforeEach(() => {
    jest.spyOn(Logger.prototype, 'error').mockImplementation(() => undefined);
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('grants each due month under a key scoped to the billed year', async () => {
    const grant = jest.fn().mockResolvedValue('granted');
    const { job } = scheduler([subscription()], grant);

    await expect(job.grantDue(at('2026-03-16T00:00:00.000Z'))).resolves.toBe(2);
    expect(grant.mock.calls).toEqual([
      ['7', 300, 'annual-allowance:sub_1:2026-01-15T12:00:00.000Z:1', expect.objectContaining({ plan: 'creator', month: 1 })],
      ['7', 300, 'annual-allowance:sub_1:2026-01-15T12:00:00.000Z:2', expect.objectContaining({ plan: 'creator', month: 2 })],
    ]);
  });

  it('only looks at active annual subscriptions with a known period', async () => {
    const { job, find } = scheduler([], jest.fn());
    await job.grantDue(at('2026-03-16T00:00:00.000Z'));
    expect(find).toHaveBeenCalledWith({
      where: expect.objectContaining({ recurringInterval: 'year', status: 'active' }) as unknown,
    });
  });

  it('counts only new grants, so a repeat run reports nothing', async () => {
    const grant = jest.fn().mockResolvedValue('duplicate');
    const { job } = scheduler([subscription()], grant);
    await expect(job.grantDue(at('2026-03-16T00:00:00.000Z'))).resolves.toBe(0);
  });

  it('keeps going when one subscription fails', async () => {
    const grant = jest
      .fn()
      .mockImplementation((userId: string) => (userId === '1' ? Promise.reject(new Error('boom')) : Promise.resolve('granted')));
    const { job } = scheduler([subscription({ user_id: '1' }), subscription({ user_id: '2', polarSubscriptionId: 'sub_2' })], grant);

    await expect(job.grantDue(at('2026-02-16T00:00:00.000Z'))).resolves.toBe(1);
    expect(grant).toHaveBeenCalledWith('2', 300, expect.stringContaining('sub_2'), expect.anything());
  });

  it('never grants for a plan it does not recognise', async () => {
    const grant = jest.fn();
    const { job } = scheduler([subscription({ plan: 'free' }), subscription({ plan: 'enterprise' })], grant);
    await expect(job.grantDue(at('2026-06-16T00:00:00.000Z'))).resolves.toBe(0);
    expect(grant).not.toHaveBeenCalled();
  });
});
