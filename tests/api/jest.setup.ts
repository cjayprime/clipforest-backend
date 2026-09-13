/**
 * src/config.ts throws on missing required variables, and some units under test
 * import it. Provide inert values so the suite runs without a .env — real
 * settings, when present, still win.
 */
process.env.DATABASE_URL ||= 'postgresql://test:test@localhost:5432/test';
process.env.S3_ACCESS_KEY_ID ||= 'test';
process.env.S3_SECRET_ACCESS_KEY ||= 'test';
process.env.PUBLIC_WEB_URL ||= 'http://localhost:3000';
process.env.JWT_SECRET ||= 'test-only-secret';
