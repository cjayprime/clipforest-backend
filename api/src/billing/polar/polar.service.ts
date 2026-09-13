import { Injectable, Logger } from '@nestjs/common';
import { config } from '../../config';
import { Errors } from '../../common/errors';

/**
 * Polar's REST API: creating a checkout, and a short-lived customer portal link.
 *
 * Checkout is confirmed against Polar's reference: POST /v1/checkouts/ with a
 * Bearer token, body { products, success_url, customer_email,
 * external_customer_id, metadata }, answering { id, url }.
 */
const CHECKOUT_PATH = '/v1/checkouts/';
/**
 * Inferred from the SDK method `polar.customerSessions.create({ customerId })`
 * returning `customerPortalUrl`. NOT confirmed against the REST reference —
 * verify this path and field the first time real credentials are wired up.
 */
const CUSTOMER_SESSION_PATH = '/v1/customer-sessions/';

export interface CreatedCheckout {
  id: string;
  url: string;
}

@Injectable()
export class PolarService {
  private readonly logger = new Logger(PolarService.name);

  get configured(): boolean {
    return Boolean(config.polar.accessToken);
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    if (!config.polar.accessToken) throw Errors.billingNotConfigured();
    const url = new URL(path, config.polar.apiBase).toString();

    let res: Response;
    try {
      res = await fetch(url, {
        method: 'POST',
        headers: {
          authorization: `Bearer ${config.polar.accessToken}`,
          'content-type': 'application/json',
          accept: 'application/json',
        },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(15_000),
      });
    } catch (err) {
      this.logger.error({ err, path }, 'Polar request failed');
      throw Errors.billingUnavailable();
    }

    const text = await res.text();
    if (!res.ok) {
      // Never log the body verbatim: it echoes customer email addresses.
      this.logger.error({ status: res.status, path }, 'Polar rejected the request');
      throw res.status >= 500 ? Errors.billingUnavailable() : Errors.billingNotConfigured();
    }
    try {
      return JSON.parse(text) as T;
    } catch {
      this.logger.error({ path }, 'Polar returned a non-JSON body');
      throw Errors.billingUnavailable();
    }
  }

  /**
   * `external_customer_id` is our own user id, which is how the resulting
   * webhooks are matched back to an account without trusting the email.
   */
  async createCheckout(input: {
    productId: string;
    userId: string;
    email: string;
    successUrl: string;
  }): Promise<CreatedCheckout> {
    const created = await this.post<{ id: string; url: string }>(CHECKOUT_PATH, {
      products: [input.productId],
      success_url: input.successUrl,
      customer_email: input.email,
      external_customer_id: input.userId,
      metadata: { userId: input.userId },
    });
    return { id: created.id, url: created.url };
  }

  /** Short-lived: generate on demand, never cache. */
  async createPortalUrl(polarCustomerId: string): Promise<string> {
    const created = await this.post<{ customer_portal_url?: string; customerPortalUrl?: string }>(
      CUSTOMER_SESSION_PATH,
      { customer_id: polarCustomerId },
    );
    const url = created.customer_portal_url ?? created.customerPortalUrl;
    if (!url) throw Errors.billingUnavailable();
    return url;
  }
}
