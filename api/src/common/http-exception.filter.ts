import { ArgumentsHost, Catch, ExceptionFilter, HttpException, Logger } from '@nestjs/common';
import { ThrottlerException } from '@nestjs/throttler';
import type { Request, Response } from 'express';
import { AppError } from './errors';

/** Renders every error in the PRD §12.3 envelope with a correlation ID. */
@Catch()
export class AllExceptionsFilter implements ExceptionFilter {
  private readonly logger = new Logger('HttpError');

  catch(exception: unknown, host: ArgumentsHost) {
    const ctx = host.switchToHttp();
    const req = ctx.getRequest<Request & { id?: string }>();
    const res = ctx.getResponse<Response>();
    const correlationId = String(req.id ?? req.headers['x-correlation-id'] ?? '');

    let status = 500;
    let code = 'SYSTEM_INTERNAL';
    let message = 'Something went wrong on our side. Please try again.';
    let retryable = true;
    let details: unknown;

    if (exception instanceof AppError) {
      status = exception.status;
      code = exception.code;
      message = exception.message;
      retryable = exception.retryable;
      details = exception.details;
    } else if (exception instanceof ThrottlerException) {
      status = 429;
      code = 'RATE_LIMITED';
      message = 'Too many requests. Please wait a moment and try again.';
      retryable = true;
    } else if (exception instanceof HttpException) {
      status = exception.getStatus();
      const body = exception.getResponse() as string | { message?: string | string[] };
      const raw = typeof body === 'string' ? body : body.message;
      message = Array.isArray(raw) ? raw.join('; ') : raw || exception.message;
      retryable = status >= 500;
      if (status === 400) {
        code = 'VALIDATION_FAILED';
        details = Array.isArray(raw) ? raw : undefined;
      } else if (status === 404) {
        code = 'NOT_FOUND';
      } else if (status === 401) {
        code = 'AUTH_REQUIRED';
      } else if (status === 413) {
        code = 'REQUEST_TOO_LARGE';
      } else {
        code = `HTTP_${status}`;
      }
    }

    if (status >= 500) {
      this.logger.error({ err: exception, correlationId, code }, 'Request failed');
    }
    if (res.headersSent) return;
    res.status(status).json({
      error: { code, message, retryable, correlationId, ...(details ? { details } : {}) },
    });
  }
}
