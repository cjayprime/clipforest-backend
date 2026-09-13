/**
 * Credit arithmetic. Each grant is split when issued: `rollover` accumulates
 * across months, `expiring` is replaced by the next grant. Debits (expiring
 * first) are made by the worker's db.py `charge_credits`.
 */

export interface Balance {
  rollover: number;
  expiring: number;
}

export function totalCredits(balance: Balance): number {
  return balance.rollover + balance.expiring;
}

/**
 * Whole credits only, and the two parts always sum to `credits` exactly — the
 * remainder goes to the expiring bucket so rounding can never mint credits.
 */
export function splitGrant(credits: number, rolloverShare = 0.9): Balance {
  if (!Number.isFinite(credits) || credits < 0) throw new RangeError('credits must be a non-negative number');
  if (!Number.isFinite(rolloverShare) || rolloverShare < 0 || rolloverShare > 1) {
    throw new RangeError('rolloverShare must be between 0 and 1');
  }
  const whole = Math.floor(credits);
  const rollover = Math.floor(whole * rolloverShare);
  return { rollover, expiring: whole - rollover };
}

/**
 * Applies a period's allowance: the rollover part is *added* to what survived,
 * the expiring part *replaces* last period's leftovers.
 */
export function applyGrant(balance: Balance, credits: number, rolloverShare?: number): Balance {
  const split = splitGrant(credits, rolloverShare);
  return { rollover: balance.rollover + split.rollover, expiring: split.expiring };
}

/**
 * What processing a video costs: credits per *started* minute, so a 61-second
 * video costs two. Mirrors the worker's credits.py `processing_cost` — the worker
 * charges it and the API uses it to refuse work up front, so they must agree.
 */
export function processingCost(durationMs: number, creditsPerMinute: number): number {
  if (durationMs <= 0 || creditsPerMinute <= 0) return 0;
  return Math.ceil(durationMs / 60_000) * creditsPerMinute;
}
