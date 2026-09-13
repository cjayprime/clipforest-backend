// @ts-check
import eslint from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';

/**
 * typescript-eslint `strictTypeChecked` + `stylisticTypeChecked`, type-aware.
 *
 * Lives at the backend root because a flat config only applies to files beneath
 * its own directory, and it must cover both api/src and tests/api.
 */
export default tseslint.config(
  {
    ignores: [
      '**/node_modules/**',
      'api/dist/**',
      'api/coverage/**',
      // Python, linted separately.
      'worker/**',
      'eslint.config.mjs',
    ],
  },

  eslint.configs.recommended,
  tseslint.configs.strictTypeChecked,
  tseslint.configs.stylisticTypeChecked,

  {
    languageOptions: {
      globals: { ...globals.node },
      parserOptions: {
        // Resolves each file to its nearest tsconfig, so api/src and tests/api
        // are both type-checked against the right project.
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      // A Nest `@Module()` is an empty decorated class by design.
      '@typescript-eslint/no-extraneous-class': 'off',
      // Entity and DTO properties are declared here and assigned by TypeORM /
      // class-transformer, so definite-assignment is the intended shape.
      '@typescript-eslint/no-non-null-assertion': 'off',

      /**
       * Empty string means "absent" throughout this codebase: `process.env.X || undefined`
       * and `input?.trim() || fallback` are deliberate. Rewriting them to `??` would
       * change behaviour — an empty POLAR_ACCESS_TOKEN would read as configured and we
       * would send `Authorization: Bearer ` to Polar. The rule still applies everywhere
       * that is not a string.
       */
      '@typescript-eslint/prefer-nullish-coalescing': ['error', { ignorePrimitives: { string: true } }],

      // Interpolating a number is idiomatic and carries no `[object Object]` risk.
      '@typescript-eslint/restrict-template-expressions': ['error', { allowNumber: true }],
    },
  },

  {
    files: ['tests/**/*.ts'],
    languageOptions: { globals: { ...globals.jest } },
  },
);
