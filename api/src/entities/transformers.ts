import type { ValueTransformer } from 'typeorm';

/*
 * Both transformers accept undefined in `from` as well as null: the driver passes
 * undefined for a column that was not selected, which TypeORM's declared
 * signature does not admit. The guards are real — do not narrow them to satisfy
 * a linter.
 */

/** Postgres bigint arrives as a string; expose it as a number (values stay far below 2^53). */
export const bigintTransformer: ValueTransformer = {
  to: (v: number | null | undefined) => (v === null || v === undefined ? v : String(v)),
  from: (v: string | null | undefined): number | null => (v === null || v === undefined ? null : Number(v)),
};

/** `double precision` / `numeric` can come back as a string depending on the driver. */
export const numericTransformer: ValueTransformer = {
  to: (v: number | null | undefined) => v,
  from: (v: string | number | null | undefined): number | null => (v === null || v === undefined ? null : Number(v)),
};
