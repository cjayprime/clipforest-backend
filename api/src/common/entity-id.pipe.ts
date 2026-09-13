import { Injectable, PipeTransform } from '@nestjs/common';
import { AppError } from './errors';

/**
 * What a primary key looks like on the wire: a bigint identity, positive, no
 * leading zero, within int8 range. Shared by this pipe and every DTO that
 * accepts an id, so "valid identifier" means one thing.
 */
export const ENTITY_ID_PATTERN = /^[1-9][0-9]{0,18}$/;

/** Validates an identifier taken from the URL and leaves it a string, which is how TypeORM represents bigint. */
@Injectable()
export class EntityIdPipe implements PipeTransform<string, string> {
  transform(value: string): string {
    if (!ENTITY_ID_PATTERN.test(value)) throw new AppError('VALIDATION_FAILED', 'That identifier is not valid.', 400);
    return value;
  }
}
